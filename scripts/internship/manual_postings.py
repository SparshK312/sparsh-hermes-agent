"""manual_postings.py — postings added to the board by hand, because no source can see them.

🔴 WHY THIS EXISTS (2026-10-17). Tesla is `ats_type: "manual"` (Akamai answers every
automated fetch with 403), so lane 1 never sees its board and a Tesla req reaches the
store only if an aggregator happens to list it. A sweep of Tesla's live feed that night
(read in his own Chrome through tesla.com's in-page `/cua-api/careers/job/<id>`) found
297 Winter/Spring 2027 intern reqs; the in-lane ones he had not applied to were on NO
aggregator, so the board could not show them. There was also no way to add a row by
hand: a Sheet row with no store entry is adopted as an identity-less orphan and never
rendered. This file is that way.

Each entry is a real posting, read in full by a human or an agent on the date given.
curate.py harvests these every run as their own lane (source "manual-add"); manual rows
are never stale-struck (only brand-board and wide-net rows are), so a row stays until it
is removed from this list or its status moves it off the queue.

To add one: append a dict below (company / role / url / location / cycle / jd), run
test_board_invariants.py, deploy. Keep `jd` to the role's own text (responsibilities +
requirements) so the fit pass scores it like any other row.
"""
from __future__ import annotations

from datetime import date

from internship_scraper import canonical_id

_TESLA_TERM = ("This position is expected to start January 2027 and continue through the "
               "Winter/Spring term (ending approximately May 2027) or continuing into Summer "
               "2027 if available. Minimum 12 weeks, full-time (40 hours/week), on-site.")

MANUAL_POSTINGS: list[dict] = [
    # ── Tesla Winter/Spring 2027, swept + JDs read 2026-10-17 (his call: put them on
    #    the Winter list). Tesla SI = Self-Driving/AI infrastructure, Palo Alto.
    {
        "company": "Tesla", "added": "2026-10-17",
        "role": "Internship, SI Software Engineer, Core Infrastructure (Winter/Spring 2027)",
        "url": "https://www.tesla.com/careers/search/job/285085",
        "location": "Palo Alto, California", "cycle": "Winter 2027", "req_id": "285085",
        "jd": _TESLA_TERM + """
What You'll Do
- Design & bring up SI Agentic workflows that spans across all stages of development from triaging issues to shipping features
- Design & implement backend infrastructure to handle iteration and batch processing of inference, simulation, and evaluation workloads based on real-world sensor data
- Influence architectural decisions with focus on security, scalability, reliability and high performance
- Setup & maintain monitoring, metrics and reporting systems for fine-grained observability/actionable alerting
- Work closely with the other Autopilot teams to design & implement backend components required for future features/processes
What You'll Bring
- Currently pursuing a degree in Computer Science, Computer Engineering, Software Engineering, Electrical Engineering or a related field of study with a graduation date between May 2027 - May 2028
- Proficiency with Python and/or Golang
- Experience with Linux especially in networking, virtualization and filesystems
- Experience with container orchestration tools like Kubernetes and deployment tools like Ansible or similar
- Experience with concurrency programming
- Experience with data stores like PostgreSQL and Redis
- Familiarity with CI/CD systems such as Teamcity or Github Actions is a plus""",
    },
    {
        "company": "Tesla", "added": "2026-10-17",
        "role": "Internship, Software Engineer, SI Data Infrastructure (Winter/Spring 2027)",
        "url": "https://www.tesla.com/careers/search/job/282292",
        "location": "Palo Alto, California", "cycle": "Winter 2027", "req_id": "282292",
        "jd": _TESLA_TERM + """
What You'll Do
- Influence architectural decisions with a focus on security, scalability, reliability, and high performance, contributing to the enhancement of our data processing infrastructure
- Choose technologies that will allow us to scale with our growing fleet of vehicles while efficiently utilizing cloud infrastructure and managing costs
- Collaborate closely with the other Autonomy teams to design and implement backend components required for future Autonomy features and processes
- Setup monitoring, metrics & reporting systems for fine-grained observability and actionable alerting, from car to cloud
- Author bullet-proof automated tests that will allow us to continue to merge and deploy with confidence
What You'll Bring
- Currently pursuing a degree in Computer Science, Computer Engineering, or relevant field of study with a graduation date between May 2027 - May 2028
- Must be able to relocate and work on site in Palo Alto, CA
- Practical experience using application layer languages like C++, Go, Python, Scala, Java
- Experience with Linux, networking, storage, and virtualization automation with tools like Kubernetes, Terraform, Ansible, Puppet or similar
- Experience with databases and search indexes like PostgreSQL, DynamoDB, ElasticSearch, Redis""",
    },
    {
        "company": "Tesla", "added": "2026-10-17",
        "role": "Internship, Software Engineer, SI Hardware Infrastructure (Winter/Spring 2027)",
        "url": "https://www.tesla.com/careers/search/job/285076",
        "location": "Palo Alto, California", "cycle": "Winter 2027", "req_id": "285076",
        "jd": _TESLA_TERM + """
What You'll Do
- Developing Python libraries to automate, monitor, measure, and troubleshoot workflows on SI hardware infrastructure
- Spending approximately 50% of time building automation and infrastructure, primarily in Python and other languages, with the remaining time focused on debugging, experimentation, and resolving infrastructure challenges and performance bottlenecks
- Creating and maintaining tools for infrastructure, automation, observability, and reporting to ensure system reliability and performance
- Collaborating with SI HW design and HPC teams to identify, debug, and resolve performance bottlenecks and cluster issues
- Supporting internal users by triaging errors, root-causing issues, and providing effective, maintainable solutions
- Proactively addressing potential infrastructure challenges to minimize user impact and enhance scalability and efficiency of SI hardware workloads
What You'll Bring
- Currently pursuing a degree in Computer Science, Physics, Computer Engineering, Mechatronics or related fields with a graduation date between May 2027 - May 2028
- Strong proficiency in Python and adaptability to learn new languages and frameworks
- Extensive familiarity with Linux administration and internals
- Experience or strong interest in automation, observability, and infrastructure development and deployment
- Ability to collaborate effectively with cross-functional teams to debug and optimize complex systems""",
    },
    {
        "company": "Tesla", "added": "2026-10-17",
        "role": "Internship, Site Reliability Engineer, Infrastructure Engineering (Winter/Spring 2027)",
        "url": "https://www.tesla.com/careers/search/job/281482",
        "location": "Fremont, California", "cycle": "Winter 2027", "req_id": "281482",
        "jd": _TESLA_TERM + """
What You'll Do
- Collaborate with a cross-functional team of SRE engineers, architects, and other stakeholders to understand complex application architectures, enabling the implementation of an effective top-down monitoring strategy for holistic service visibility
- Build, maintain, and monitor dashboards for critical infrastructure
- Create and tune alerts for network and hardware so that potential problems are identified, routed, and remediated early
- Facilitate knowledge sharing by creating and maintaining detailed and comprehensive documentation, diagrams, and runbooks
- Respond to and resolve support requests in a timely fashion while managing project timelines and other responsibilities
What You'll Bring
- Currently pursuing a degree in Computer Science, Information Technology, or a related field with a graduation date between 2027-2028
- Proficiency in one or more of: Python, Go, Bash/shell, or similar languages used for automation and tooling
- Solid Linux fundamentals (processes, networking, filesystems, permissions) and comfort operating in a command-line environment
- Interest in or exposure to building, running, and maintaining reliable infrastructure and services
- Strong grasp of CS fundamentals and ability to write clear, well-organized, maintainable code
- Curiosity about how systems behave in production: reliability, performance, and failure modes
- Kubernetes; Prometheus (metrics, PromQL, alerting) and related observability (Grafana, logs, tracing)
- CI/CD, infrastructure-as-code, or config management (e.g. Terraform, Ansible, GitHub Actions)
- Containers (Docker), cloud basics (AWS/GCP/Azure), or networking fundamentals""",
    },
]


def collect(today: date | None = None) -> list[dict]:
    """The manual rows in the same shape the other lanes return to curate.refresh()."""
    today = today or date.today()
    out = []
    for p in MANUAL_POSTINGS:
        added = p.get("added") or today.isoformat()
        try:
            age = (today - date.fromisoformat(added)).days
        except ValueError:
            age = None
        out.append({
            "canonical_id": canonical_id(p["url"]),
            "company": p["company"], "role": p["role"], "location": p["location"],
            "url": p["url"], "ats_type": "manual", "cycle": p.get("cycle", ""),
            "posted_date": added, "age_days": age,
            "full_jd": p.get("jd", "").strip(), "req_id": p.get("req_id", ""),
            "source": "manual-add",
        })
    return out
