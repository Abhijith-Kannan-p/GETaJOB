"""Synthetic test profiles for evals 2 and 3. Not real users: each pairs one of the role buttons on /app with
skills from the app's own "popular skills" buttons, so the evals exercise the same inputs the UI produces."""

from app.api.rank import ProfileIn

PROFILES: dict[str, ProfileIn] = {
    "backend": ProfileIn(
        skills=["Python", "SQL", "Git", "Docker", "REST APIs"], target_roles=["backend engineer intern"]
    ),
    "frontend": ProfileIn(
        skills=["JavaScript", "TypeScript", "React", "Git"], target_roles=["frontend engineer intern"]
    ),
    "fullstack": ProfileIn(
        skills=["JavaScript", "TypeScript", "React", "Node.js", "SQL"], target_roles=["full stack engineer intern"]
    ),
    "data": ProfileIn(skills=["Python", "SQL", "pandas"], target_roles=["data analyst intern"]),
    "ml": ProfileIn(
        skills=["Python", "Machine Learning", "pandas", "Git"], target_roles=["machine learning engineer intern"]
    ),
    "devops": ProfileIn(
        skills=["Linux", "Docker", "AWS", "Kubernetes", "Git"], target_roles=["devops cloud engineer intern"]
    ),
    "java": ProfileIn(
        skills=["Java", "SQL", "Git"],
        target_roles=["backend engineer intern"],
        remote_ok=False,
        locations=["Bengaluru"],
    ),
}
