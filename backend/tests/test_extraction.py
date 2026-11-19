from app.ingest import clean, skills
from app.ingest.classify import in_india, is_remote, is_tech, seniority


def found(text: str) -> list[str]:
    return [m.skill for m in skills.load_taxonomy().find(text)]


def test_special_character_skills():
    assert found("Experience with C++, C# and .NET") == ["C++", "C#", ".NET"]
    assert found("Node.js and Next.js") == ["Node.js", "Next.js"]


def test_longest_match_wins():
    assert found("Objective-C or Swift") == ["Objective-C", "Swift"]
    assert found("React Native apps") == ["React Native"]


def test_aliases_map_to_canonical():
    assert found("golang, k8s, postgres and JS") == ["Go", "Kubernetes", "PostgreSQL", "JavaScript"]


def test_case_sensitive_and_blocked_words():
    assert found("we go to market fast") == []
    assert found("Go-to person for infra") == []
    assert found("we raised our Series C") == []
    assert found("R&D team") == []
    assert found("you excel at debugging") == ["Debugging"]
    assert found("Advanced Excel") == ["Excel"]
    assert found("react quickly") == []
    assert found("Spark and pyspark") == ["Apache Spark", "Apache Spark"]


def test_inline_tags_do_not_split_words():
    lines = clean.html_to_lines("<li><strong>C</strong>ommunicate clearly</li>")
    assert lines == ["Communicate clearly"]


GH_HTML = """
<p>About us: we use Python everywhere.</p>
<h3>What you will do</h3><ul><li>Build services in Go</li></ul>
<h3>Requirements</h3><ul><li>Strong Python and SQL</li></ul>
<h3>Nice to have</h3><ul><li>Kubernetes, Python</li></ul>
<h3>Benefits</h3><ul><li>Free Docker stickers</li></ul>
"""


def test_section_buckets():
    secs = clean.sections(GH_HTML)
    kinds = [k for k, _, _ in secs]
    assert kinds == ["other", "responsibilities", "required", "preferred", "ignore"]
    ex = skills.extract(secs)
    assert ex.required == ["Python", "SQL"]
    # responsibilities/other go to preferred when a Requirements section exists; required wins on overlap
    assert set(ex.preferred) == {"Go", "Kubernetes"}
    assert "Docker" not in ex.required + ex.preferred  # benefits boilerplate ignored


def test_no_required_section_means_body_is_required():
    ex = skills.extract(clean.sections("<p>You will write TypeScript and React.</p>"))
    assert ex.required == ["TypeScript", "React"] and ex.preferred == []


def test_lever_lists_are_sections():
    secs = clean.sections("<p>Intro</p>", [("Preferred qualifications", "<li>Rust</li>")])
    assert skills.extract(secs).preferred == ["Rust"]


def test_seniority():
    assert seniority("Software Engineering Intern") == "intern"
    assert seniority("New Grad Software Engineer") == "new_grad"
    assert seniority("Senior Backend Engineer") == "senior"
    assert seniority("Software Engineer I") == "entry"
    assert seniority("Software Engineer II") == "mid"
    assert seniority("Software Engineer", "Requires 0-1 years of experience") == "entry"
    assert seniority("Software Engineer", "5+ years of experience") == "senior"
    assert seniority("Software Engineer") == "unknown"


def test_location_and_tech():
    assert is_remote("Engineer", "Remote - India")
    assert is_remote("Engineer", "Bengaluru", "remote")
    assert not is_remote("Engineer", "Bengaluru, India")
    assert in_india("Gurugram, Haryana") and not in_india("Dublin")
    assert is_tech("Machine Learning Engineer") and not is_tech("Account Executive")
    assert is_tech("Sales Engineer")
