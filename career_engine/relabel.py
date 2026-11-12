"""Replay a versioned geography label for every sighting in the local store."""
import argparse
import json
import os
import sqlite3

from career_engine.ats import LEVER_PROVIDERS, LEVER_REMOTE_LOCATION
from career_engine.geography import _classify_geo, load_gazetteer
from career_engine.store import Store

SUPPORTED_PLATFORMS = {"usajobs", "greenhouse"}


def _object(value):
    return value if isinstance(value, dict) else {}


def _objects(value):
    return (item for item in value if isinstance(item, dict)) if isinstance(value, list) else ()


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _usa_worksite(address):
    return {key: value for key, value in {
        "location": _text(address.get("LocationName")) or _text(address.get("CityName")),
        "state": _text(address.get("CountrySubDivisionCode")),
        "country": _text(address.get("CountryCode")),
    }.items() if value}


def _record_contexts(body, platform):
    data = _object(json.loads(body))
    if platform == "usajobs":
        for row in _objects(_object(data.get("SearchResult")).get("SearchResultItems")):
            descriptor = _object(row.get("MatchedObjectDescriptor"))
            sites = [_usa_worksite(address) for address in _objects(descriptor.get("PositionLocation"))]
            yield row.get("MatchedObjectId"), descriptor.get("PositionURI"), sites
    else:
        for row in _objects(data.get("jobs")):
            location = row.get("location")
            sites = [_text(_object(location).get("name")) or _text(location)]
            sites.extend(_text(office.get("location")) or _text(office.get("name"))
                         for office in _objects(row.get("offices")))
            yield row.get("id"), row.get("absolute_url"), sites


def _index_capture(blobs, sha, platform):
    try:
        body = blobs.get(sha)
    except FileNotFoundError:
        return {}
    index = {}
    try:
        for native_id, url, sites in _record_contexts(body, platform):
            places = tuple(site for site in sites if site)
            keys = []
            if type(native_id) in (str, int) and str(native_id):
                keys.append(("id", str(native_id)))
            if isinstance(url, str) and url:
                keys.append(("url", url))
            for key in keys:
                # Duplicate occurrences with conflicting location evidence cannot be joined safely.
                if key in index and index[key] != places:
                    index[key] = ()
                else:
                    index.setdefault(key, places)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return index


def relabel(store, dimension, version):
    """Append one missing label per sighting atomically, preserving prior versions."""
    if dimension != "geo":
        raise ValueError("Only the geo dimension is available")
    gazetteer = load_gazetteer(version)
    capture_key = None
    captured = {}
    added = 0
    # The writer lock makes selection plus insertion idempotent under retries
    # and concurrent relabel calls without changing historical label constraints.
    with store.transaction():
        for sighting in store.unlabeled_sightings(dimension, version):
            platform, sha = sighting["platform"], sighting["raw_sha"]
            location = sighting["location_raw"]
            context = {"title": sighting["title"]}
            if platform in LEVER_PROVIDERS:
                arrangement, separator, places = location.partition(" ; ")
                if arrangement == LEVER_REMOTE_LOCATION:
                    context["work_arrangement"] = "remote"
                    location = places if separator else ""
            if sha and platform in SUPPORTED_PLATFORMS and sighting["retention_class"] == "public":
                key = (platform, sha)
                if key != capture_key:
                    captured = _index_capture(store.blobs, sha, platform)
                    capture_key = key
                native_id = sighting["native_id"]
                identity = ("id", native_id) if native_id else ("url", sighting["url"])
                context["worksites"] = captured.get(identity, ())
            value, reason = _classify_geo(location, context, gazetteer)
            store.add_label(subject="sighting", subject_id=sighting["sighting_id"], dimension=dimension,
                            value=value, method="gazetteer: " + reason, classifier_version=version)
            added += 1
    return added


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=os.environ.get("PROSPECT_DATA_DIR"),
                        help="Directory containing prospect.sqlite (default: PROSPECT_DATA_DIR)")
    parser.add_argument("--dimension", required=True, choices=("geo",))
    parser.add_argument("--version", required=True, help="Bundled geography classifier version")
    args = parser.parse_args(argv)
    if not args.data_dir:
        parser.error("Supply --data-dir or set PROSPECT_DATA_DIR")
    try:
        with Store.in_data_dir(args.data_dir, create=False) as store:
            added = relabel(store, args.dimension, args.version)
    except (ValueError, OSError, EOFError, sqlite3.Error) as error:
        parser.exit(1, f"Relabel failed: {error}\n")
    print(f"Appended {added} {args.dimension} labels for {args.version}")


if __name__ == "__main__":
    main()
