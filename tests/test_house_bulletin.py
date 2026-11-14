"""Boundary, local MIME, PDF and ledger tests; no network or sealed inputs."""
import csv
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from career_engine.house_bulletin import PDFReadError, pdf_layout, reconcile_layout
from career_engine.house_bulletin_intake import import_bulletins
from tools.import_house_bulletin import main, publish_import
from tools.validate_house_bulletin import validate

HEAD = "Member and Committee Openings\nWeek of May 11, 2026\n"
A = "MEM-111-26\nPosition Available: Caseworker\nLocation: Houston, Texas\nTo apply: email jobs@example.org by May 20, 2026."
B = "MEM-115-26\nPosition Available: Staff Assistant\nOffice/Location: Washington, DC\nPlease submit a resume."
TIME1 = "2026-11-07T17:00:00+00:00"
TIME2 = "2026-11-07T18:00:00+00:00"
POPLER = shutil.which("pdfinfo") and shutil.which("pdftotext")


def pdf_bytes(text=None):
    # A one-page document; text=None produces an actual image-only PDF.
    if text is None:
        resource = b"<< /XObject << /Im0 5 0 R >> >>"
        stream = b"q 20 0 0 20 30 30 cm /Im0 Do Q"
        obj5 = (b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
                b"/ColorSpace /DeviceGray /BitsPerComponent 8 /Length 1 >>\nstream\n\x00\nendstream")
    else:
        resource = b"<< /Font << /F1 5 0 R >> >>"
        escaped = [s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for s in text.splitlines()]
        stream = ("BT /F1 12 Tf 15 TL 25 760 Td " + " T* ".join(f"({s}) Tj" for s in escaped) + " ET").encode()
        obj5 = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources " + resource + b" /Contents 4 0 R >>",
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream", obj5]
    body = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objs, 1):
        offsets.append(len(body))
        body.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(body)
    body.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        body.extend(f"{offset:010d} 00000 n \n".encode())
    body.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(body)


def make_mail(text="", html=None):
    msg = EmailMessage()
    msg["Message-ID"] = "<test-bulletin@example.invalid>"
    msg["Date"] = "Mon, 11 May 2026 09:00:00 -0400"
    msg["Received"] = "by private.example.invalid; Mon, 11 May 2026 09:04:00 -0400"
    msg["From"] = "sender@example.invalid"
    msg["To"] = "recipient@example.invalid"
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


class LayoutTests(unittest.TestCase):
    def test_retain_non_dc_and_do_not_infer_publication_or_current_vacancy(self):
        d = reconcile_layout(HEAD + A + "\n\n" + B + "\f", 1)
        a, b = d["records"]
        self.assertEqual((a["location"], a["geography"]), ("Houston, Texas", "elsewhere"))
        self.assertEqual(b["geography"], "dc_dmv")
        self.assertEqual(a["closing_at"], "2026-05-20")
        self.assertEqual(a["posted_at"], "")
        self.assertIn("current_availability_unverified", a["evidence_status"])
        self.assertEqual(a["application_instructions"], "To apply: email jobs@example.org by May 20, 2026.")
        self.assertEqual(a["published_emails"], ["jobs@example.org"])
        self.assertEqual(d["number_gaps_review_only"], {"26": [112, 113, 114]})
        self.assertEqual(d["diagnostics"], [])

    def test_nested_mentions_not_counted_as_new_positions(self):
        layout = HEAD + A + '\nUse reference number:\nMEM-111-26\nwhen submitting. Also see MEM-200-26.\n\n' + B
        d = reconcile_layout(layout, 1)
        self.assertEqual([h["kind"] for h in d["occurrences"]], ["heading", "reference", "reference", "heading"])
        self.assertEqual(len(d["records"]), 2)
        self.assertEqual(d["unresolved_ids"], ["MEM-200-26"])
        self.assertEqual(d["completeness"], "PARTIAL COLLECTION")
        self.assertIn("Also see MEM-200-26", d["records"][0]["description"])

    def test_duplicate_heading_retains_both_raw_sections_one_row(self):
        layout = HEAD + A + "\n\n" + A.replace("Caseworker", "Constituent Advocate")
        d = reconcile_layout(layout, 1)
        self.assertEqual(len(d["records"]), 1)
        self.assertEqual(len(d["records"][0]["raw_sections"]), 2)
        self.assertIn("duplicate_numbered_sections", {x["code"] for x in d["diagnostics"]})

    def test_split_id_across_line_and_page_retains_positions_and_continuation(self):
        layout = HEAD + "MEM-\n111-\f26\nPosition Available: Staff Assistant\fThis paragraph continues.\n\n" + B + "\f"
        d = reconcile_layout(layout, 3)
        hit = d["occurrences"][0]
        self.assertEqual((hit["page"], hit["end_page"], hit["id"]), (1, 2, "MEM-111-26"))
        section = d["records"][0]["raw_sections"][0]
        self.assertEqual(section["end_page"], 3)
        self.assertEqual(section["raw_text"], layout[section["start"]["offset"]:section["end_offset_exclusive"]])
        self.assertIn("This paragraph continues.", section["raw_text"])

    def test_malformed_ambiguous_and_orphan_material_remains_partial(self):
        d = reconcile_layout(HEAD + "Orphan job material\nMEM-003-26 looks like a reference\nMEM-12-26\n" + B, 2)
        codes = {i["code"] for i in d["diagnostics"]}
        self.assertTrue({"page_count_mismatch", "ambiguous_mem_boundary", "malformed_mem_token",
                         "unassigned_preface_material"} <= codes)
        self.assertIn("Orphan job material", d["unassigned_preface"])
        self.assertEqual(d["unresolved_ids"], ["MEM-003-26"])

    def test_unknown_fields_and_no_mem_sections_are_not_successful_empty(self):
        d = reconcile_layout(HEAD + "MEM-001-26\nAmbiguous prose with no extractable fields.", 1)
        row = d["records"][0]
        for key in ("advertised_title", "office", "advertised_worksite", "application_instructions", "application_deadline"):
            self.assertIsNone(row[key])
        self.assertEqual(row["geography"], "unknown")
        d = reconcile_layout("An unnumbered notice.", 1)
        self.assertEqual(d["records"], [])
        self.assertEqual(d["completeness"], "PARTIAL COLLECTION")
        self.assertEqual(d["unassigned_preface"], "An unnumbered notice.")

    def test_missing_text_page_and_suspicious_glyphs_are_partial(self):
        d = reconcile_layout(HEAD + A + "\f\f" + B + "\nThe ofice is hiring.\f", 3)
        self.assertEqual(len(d["records"]), 2)
        self.assertIn({"code": "pages_without_text_no_ocr", "pages": [2]}, d["diagnostics"])
        self.assertIn("possible_text_layer_glyph_loss", {i["code"] for i in d["diagnostics"]})

    def test_issue_date_conflict_does_not_rekey_annual_identifier(self):
        d = reconcile_layout(HEAD.replace("2026", "2025") + A, 1)
        self.assertEqual(d["records"][0]["identity"], "house_bulletin:MEM-111-26")
        self.assertIn("issue_date_id_year_mismatch", {i["code"] for i in d["diagnostics"]})


class PDFTests(unittest.TestCase):
    def test_empty_non_pdf_and_corrupt_bytes(self):
        for payload, code in [(b"", "pdf_empty_or_size_limit"), (b"not pdf", "not_a_pdf")]:
            with self.assertRaisesRegex(PDFReadError, code):
                pdf_layout(payload)

    @unittest.skipUnless(POPLER, "Poppler is required for PDF integration")
    def test_real_pdf_extraction_image_only_and_corrupt(self):
        with self.assertRaisesRegex(PDFReadError, "pdf_unreadable_or_corrupt"):
            pdf_layout(b"%PDF-1.7\ntruncated")
        data = reconcile_layout(*pdf_layout(pdf_bytes()))
        self.assertEqual(data["records"], [])
        self.assertIn("pages_without_text_no_ocr", {x["code"] for x in data["diagnostics"]})
        self.assertEqual(data["completeness"], "PARTIAL COLLECTION")
        self.assertEqual(len(reconcile_layout(*pdf_layout(pdf_bytes(HEAD + A)))["records"]), 1)

    @unittest.skipUnless(os.getenv("HOUSE_BULLETIN_FIXTURES") and POPLER, "set HOUSE_BULLETIN_FIXTURES to the two verified historical PDFs")
    def test_historical_publisher_pdfs_every_id_and_page_span(self):
        report = validate(os.environ["HOUSE_BULLETIN_FIXTURES"])
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["annual_unique_ids"], 40)
        self.assertEqual(len(report["cross_issue_ids"]), 4)


@unittest.skipUnless(POPLER, "Poppler is required for import integration")
class IntakeTests(unittest.TestCase):
    def run_import(self, root, paths, at=TIME1, url=""):
        result = import_bulletins(paths, root, lambda c, r, n: publish_import(root, c, r, n), url, at)
        folder = Path(result["import_folder"])
        return result, json.loads((folder / "house-bulletin.json").read_text())

    def test_two_issues_and_exact_replay_preserve_history_and_artifact_hashes(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            one, two = p / "older.pdf", p / "newer.pdf"
            one.write_bytes(pdf_bytes(HEAD.replace("May 11", "May 4") + A))
            two.write_bytes(pdf_bytes(HEAD + A + "\n\n" + B))
            root = p / "ledger"
            self.run_import(root, [one])
            _, data = self.run_import(root, [two], TIME2, "https://publisher.example.org/bulletin.pdf")
            row = next(r for r in data["records"] if r["source_id"] == "MEM-111-26")
            self.assertTrue(row["cross_issue_republication"])
            self.assertEqual(row["observed_issue_dates"], ["2026-05-04", "2026-05-11"])
            self.assertEqual((row["first_observed_at"], row["last_observed_at"]), (TIME1, TIME2))
            self.assertEqual(row["duplicate_status"], "previously_observed_annual_mem_id")
            self.assertEqual(row["url"], "https://publisher.example.org/bulletin.pdf#page=1")
            self.run_import(root, [two], TIME2)
            state = json.loads((root / "vacancy-ledger.json").read_text())
            self.assertEqual(len(state["vacancies"]), 2)
            self.assertEqual(len(state["vacancies"]["MEM-111-26"]["sightings"]), 2)
            for doc in state["documents"].values():
                a = doc["artifact"]
                self.assertEqual(hashlib.sha256((root / a["source_artifact"]).read_bytes()).hexdigest(), a["sha256"])
            self.assertEqual(len(list((root / "imports").iterdir())), 3)

    def test_distinct_annual_suffixes_are_distinct_and_same_week_variants_not_cross_week(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            files = [p / f"{i}.pdf" for i in range(3)]
            for file, text in zip(files, [HEAD + A, HEAD + A.replace("Caseworker", "Advisor"),
                                        (HEAD + A).replace("2026", "2025").replace("111-26", "111-25")]):
                file.write_bytes(pdf_bytes(text))
            _, data = self.run_import(p / "out", files)
            row = next(r for r in data["records"] if r["source_id"] == "MEM-111-26")
            self.assertFalse(row["cross_issue_republication"])
            self.assertEqual(row["content_versions"], 2)
            self.assertEqual(set(data["retained_ids"]), {"MEM-111-25", "MEM-111-26"})

    def test_multiple_mime_attachments_parent_and_discrepancies_without_remote_fetch(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            url = "https://content.govdelivery.com/attachments/USCAOHOUSE/example/weekly.pdf"
            msg = make_mail("MEM-111-26\nDifferent body listing.\nMEM-300-26\nBody only.\n" + url,
                            '<p>MEM-300-26</p><img src="https://tracking.example.org/pixel"><script>MEM-999-26</script>')
            msg.add_attachment(pdf_bytes(HEAD + A), maintype="application", subtype="pdf", filename="weekly.pdf")
            msg.add_attachment(pdf_bytes(HEAD + B), maintype="application", subtype="octet-stream", filename="second.pdf")
            msg.add_attachment(b"<script>never execute</script>", maintype="text", subtype="html", filename="../unsafe.html")
            file = p / "source.eml"
            raw = msg.as_bytes()
            file.write_bytes(raw)
            with patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
                result, data = self.run_import(p / "out", [file])
            self.assertEqual(result["status"], "PARTIAL COLLECTION")
            self.assertEqual(set(data["retained_ids"]), {"MEM-111-26", "MEM-115-26"})
            mail = next(iter(data["messages"].values()))
            self.assertEqual(mail["sent_at"], "2026-05-11T13:00:00+00:00")
            self.assertEqual(mail["received_at"], "2026-05-11T13:04:00+00:00")
            self.assertEqual(mail["artifact"]["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(len(mail["attachments"]), 2)
            self.assertEqual(mail["body_unique_ids"], ["MEM-111-26", "MEM-300-26"])
            self.assertEqual(mail["remote_resources_fetched"], 0)
            ledger = json.loads((p / "out/vacancy-ledger.json").read_text())
            saved = ledger["messages"][mail["artifact"]["sha256"]]
            self.assertEqual(saved["message_identity"], mail["message_identity"])
            self.assertEqual(saved["received_at"], mail["received_at"])
            codes = {item["code"] for item in mail["diagnostics"]}
            self.assertTrue({"email_body_ids_not_in_pdf_attachments", "pdf_ids_not_in_numbered_email_body",
                             "email_body_section_differs_from_pdf", "unsupported_mime_attachment_not_processed"} <= codes)
            self.assertNotIn("recipient@example.invalid", json.dumps(data))
            self.assertNotIn("sender@example.invalid", json.dumps(data))
            self.assertIn(url, [u for doc in data["documents"].values() for u in doc["source_urls"]])

    def test_email_pdf_link_only_is_not_fetched_and_no_job_is_invented(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            file = p / "link.eml"
            file.write_bytes(make_mail("See https://content.govdelivery.com/attachments/USCAOHOUSE/a/b.pdf").as_bytes())
            result, data = self.run_import(p / "out", [file])
            self.assertEqual(result["status"], "PARTIAL COLLECTION")
            self.assertFalse(data["records"])
            self.assertIn("no_pdf_attachments_remote_links_not_fetched", result["diagnostic_codes"])

    def test_corrupt_pdf_does_not_erase_prior_good_or_other_input(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            a, b = p / "good.pdf", p / "bad.pdf"
            a.write_bytes(pdf_bytes(HEAD + A))
            b.write_bytes(b"%PDF-1.4\npartial")
            result, data = self.run_import(p / "out", [a, b])
            self.assertEqual(result["retained"], 1)
            self.assertEqual(result["status"], "PARTIAL COLLECTION")
            self.assertEqual(len(data["documents"]), 2)

    def test_batch_document_limit_keeps_earlier_evidence_and_reports_skipped_input(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            a, b = p / "first.pdf", p / "second.pdf"
            a.write_bytes(pdf_bytes(HEAD + A))
            b.write_bytes(pdf_bytes(HEAD + B))
            with patch("career_engine.house_bulletin_intake.MAX_BATCH_PDFS", 1):
                result, data = self.run_import(p / "out", [a, b])
            self.assertEqual(result["status"], "PARTIAL COLLECTION")
            self.assertEqual(data["retained_ids"], ["MEM-111-26"])
            skipped = next(d for d in data["diagnostics"] if d["code"] == "batch_pdf_limit")
            self.assertEqual(skipped["sha256"], hashlib.sha256(b.read_bytes()).hexdigest())
            self.assertIsNone(skipped["source_artifact"])

    @unittest.skipUnless(os.getenv("HOUSE_BULLETIN_FIXTURES"), "set HOUSE_BULLETIN_FIXTURES for historical PDFs")
    def test_authentic_pdfs_in_synthetic_exported_email(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            fixtures = Path(os.environ["HOUSE_BULLETIN_FIXTURES"])
            msg = make_mail("Synthetic test envelope; see attached historical PDFs.")
            for name in ("2026-04-20.pdf", "2026-05-11.pdf"):
                msg.add_attachment((fixtures / name).read_bytes(), maintype="application", subtype="pdf", filename=name)
            path = p / "synthetic-envelope.eml"
            path.write_bytes(msg.as_bytes())
            result, data = self.run_import(p / "out", [path])
            self.assertEqual(result["retained"], 40)
            self.assertEqual(sum(row["cross_issue_republication"] for row in data["records"]), 4)
            self.assertEqual(len(data["documents"]), 2)
            self.assertEqual(len(next(iter(data["messages"].values()))["attachments"]), 2)
            self.assertEqual({d["audit"]["issue_date"] for d in data["documents"].values()}, {"2026-04-20", "2026-05-11"})

    def test_failed_report_and_tampered_evidence_do_not_overwrite_good_state(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            a = p / "good.pdf"
            a.write_bytes(pdf_bytes(HEAD + A))
            root = p / "out"
            self.run_import(root, [a])
            ledger = root / "vacancy-ledger.json"
            original = ledger.read_bytes()
            def broken(*args):
                raise OSError("disk")
            with self.assertRaises(OSError):
                import_bulletins([a], root, broken)
            self.assertEqual(ledger.read_bytes(), original)
            next((root / "evidence").glob("*.pdf")).write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "stored_source_evidence_hash_mismatch"):
                self.run_import(root, [a])
            self.assertEqual(ledger.read_bytes(), original)

    def test_csv_markdown_and_publication_ledger_and_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            file = p / "valid.pdf"
            file.write_bytes(pdf_bytes(HEAD + A))
            result, data = self.run_import(p / "out", [file])
            folder = Path(result["import_folder"])
            with (folder / "house-bulletin.csv").open(newline="") as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(row["description"], data["records"][0]["description"])
            self.assertIn("Local source", (folder / "house-bulletin.md").read_text())
            coverage = json.loads((folder / "coverage-audit.json").read_text())
            self.assertEqual(coverage["schema_version"], "publication-coverage-ledger/v1")
            self.assertIsNone(coverage["sources"][0]["inventory"]["publisher_inventory_total"])
            with patch("builtins.print"):
                self.assertEqual(main([str(file), "--output-dir", str(p / "out")]), 0)
                self.assertEqual(main([str(file), "--output-dir", str(p / "out"), "--source-url",
                                       "https://publisher.example.org/bulletin.pdf?token=x"]), 1)
                file.write_bytes(b"")
                self.assertEqual(main([str(file), "--output-dir", str(p / "out")]), 2)


if __name__ == "__main__":
    unittest.main()
