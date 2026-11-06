# Real recovered records: publication coverage, not a ranked shortlist

These examples were actually retrieved during the November 5, 2026 Denver
collection window. They are selected to explain employer/channel diversity and
retrieval boundaries, not to represent every record or independently validate
candidate fit. Employer/ATS publication does not prove applications remain open.
Qualifications below paraphrase the observed description; the source URL and
description hash are in [the metadata audit](actual-dc-records.jsonl).

| Employer / publication | Record and location evidence | Function and observed qualifications | Increment and limits |
|---|---|---|---|
| Brennan Center for Justice — public JazzHR board | [Program Associate, Elections and Government Program](https://brennancenter.applytojob.com/apply/IqgNxPIqoH/Program-Associate-Elections-And-Government-Program). Provider labels DC; description explicitly offers New York headquarters **or** Washington office. | Policy-program administration, research, writing, project logistics and stakeholder communication. Bachelor's degree and at least one year of work experience required; writing, fact-checking, organization and research skills. | Absent from both the 303-record specialty baseline and legacy arm. An actual early-career policy/program route. Employer was discovered through sitemap job URLs, including a NYC-and-DC geography suffix, not a hardcoded seed. Five local board records recovered, including student-specific internships that require separate eligibility checks. |
| Threshold Giving — public JazzHR board | [Humanitarian Advocate & Fundraiser for the International Rescue Committee](https://thresholdgroup.applytojob.com/apply/NKszi41rvh/Humanitarian-Advocate-Fundraiser-For-The-International-Rescue-Committee). Description explicitly includes Washington DC and Seattle. | Paid public-facing fundraising for a client charity. Communication, outdoor public interaction and reliability required. Prior sales/fundraising experience is a plus, not mandatory. Two weeks of full-time training required; part-time schedules require weekends. | New to baseline and legacy arm; credible development/outreach adjacency, not a policy research role. Employer is **Threshold**, not automatically the charity client. Performance-dependent compensation and working conditions need review. The separate Canvass Director requires completed trainer/team-leader training. |
| The Gathering Spot — public JazzHR board | [Community Concierge, DC](https://thegatheringspot.applytojob.com/apply/jmL4zVK3M5/Community-Concierge-The-Gathering-Spot-DC). DC-specific title and published board location. | Membership systems, reservations, correspondence, event promotion, administrative support and troubleshooting. Required communication, Office, organization and problem-solving skills. Two years of hotel guest services experience is highly preferred. | New to baseline and legacy arm; membership/administration adjacency rather than a policy employer. Physical lifting requirement; no inference that preferred experience is mandatory. Other recovered records include food service, management and a future-openings pool; do not count all five as equivalent employment opportunities. |
| CleanChoice Energy — public JazzHR board | [Pricing Analyst](https://cleanchoiceenergy.applytojob.com/apply/SHkVdFEsxM/Pricing-Analyst). Description names Washington DC. | Quantitative degree; 1–3 years in analytical work such as pricing, energy, finance or consulting; advanced Excel plus working SQL, Python or VBA required. Clear communication and analytical accuracy. Energy-market experience is preferred. | New to baseline and legacy arm; commercial research/analysis adjacency, with substantive technical qualifications. A junior-looking title is not enough to establish eligibility. |

Important controls remain visible:

- **MOTA DC appointments:** 42 locally labelled records. Four inspected descriptions
  explicitly state the boards are unpaid/voluntary and are excluded from the
  conservative employment-gain diagnostic. The other 38 are uninspected, not
  verified paid jobs. This one channel substantially inflates raw local volume.
- **CCMI and Gathering Spot pools:** two inspected assignment-registration notices
  and one explicit future-opening notice are retained and flagged as pools.
  They do not establish defined open vacancies.
- **Context Travel metadata conflict:** a Montréal tour-guide title was published
  with a Washington DC provider label. The engine records the conflict and
  treats geography as unknown. The minimized actual metadata is an executable
  regression fixture; it does not get counted as a DC gain.
- **Impact Opportunity / Pew Associate I:** a nonprofit RSS local record was also
  present in Work for Nonprofits. It is a probable cross-post, not incremental
  employer coverage. Its published body did not establish complete mandatory
  qualifications. A nonempty description is not a complete requirements audit.
- **NNU, R Street and technical contractors:** existing source cross-posts,
  senior subject-matter roles, specialist qualifications and unclear records
  remain in the collection. No keyword rejects or personal-fit scores remove
  them during discovery.

JazzHR publication discovery produced the clearest additional nontechnical
examples in this test. The RSS adapter expanded the nonprofit publication surface
but did not demonstrate an additional early-career local opening in its returned
window. Optional HQ-directory traversal produced no supported board additions.
Consulting, associations, lobbying and government affairs still have material
uncovered/custom/restricted inventories. See [the assessment](ASSESSMENT.md) for
denominators, access failures and both unsuccessful real Tampa cold starts.
