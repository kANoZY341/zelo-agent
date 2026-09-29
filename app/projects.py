"""Durable v0.12 Project and Goal ownership service."""

from sqlalchemy import func, select

from app.models import Goal, Project, utcnow
from app.policy import DomainError
from app.schemas import GoalCreate, GoalResponse, ProjectCreate, ProjectResponse
from app.storage import SessionStore


class ProjectService:
    def __init__(self, database: SessionStore) -> None:
        self.database = database

    @staticmethod
    def _project_response(project: Project) -> ProjectResponse:
        return ProjectResponse.model_validate(project)

    @staticmethod
    def _goal_response(goal: Goal) -> GoalResponse:
        return GoalResponse.model_validate(goal)

    def create(self, body: ProjectCreate) -> ProjectResponse:
        name = " ".join(body.name.split())
        if not name:
            raise DomainError("Project name cannot be blank", 422)
        summary = body.summary.strip()
        with self.database.transaction() as session:
            project = Project(name=name, summary=summary, status="active")
            session.add(project)
            session.flush()
            return self._project_response(project)

    def get(self, project_id: str) -> ProjectResponse:
        with self.database.read() as session:
            project = session.get(Project, project_id)
            if project is None:
                raise DomainError("Project not found", 404)
            return self._project_response(project)

    def list_projects(self, limit: int, offset: int) -> tuple[list[ProjectResponse], int]:
        with self.database.read() as session:
            total = session.scalar(select(func.count()).select_from(Project)) or 0
            projects = list(
                session.scalars(
                    select(Project)
                    .order_by(Project.updated_at.desc(), Project.id)
                    .offset(offset)
                    .limit(limit)
                )
            )
            return [self._project_response(project) for project in projects], total

    def create_goal(self, project_id: str, body: GoalCreate) -> GoalResponse:
        title = " ".join(body.title.split())
        if not title:
            raise DomainError("Goal title cannot be blank", 422)
        with self.database.transaction() as session:
            project = session.get(Project, project_id)
            if project is None:
                raise DomainError("Project not found", 404)
            goal = Goal(
                project_id=project.id,
                title=title,
                spec=body.spec.model_dump(mode="json"),
                status="active",
            )
            session.add(goal)
            project.updated_at = utcnow()
            session.flush()
            return self._goal_response(goal)

    def list_goals(
        self,
        project_id: str,
        limit: int,
        offset: int,
    ) -> tuple[list[GoalResponse], int]:
        with self.database.read() as session:
            project = session.get(Project, project_id)
            if project is None:
                raise DomainError("Project not found", 404)
            total = (
                session.scalar(
                    select(func.count()).select_from(Goal).where(Goal.project_id == project_id)
                )
                or 0
            )
            goals = list(
                session.scalars(
                    select(Goal)
                    .where(Goal.project_id == project_id)
                    .order_by(Goal.updated_at.desc(), Goal.id)
                    .offset(offset)
                    .limit(limit)
                )
            )
            return [self._goal_response(goal) for goal in goals], total

    def context_snapshot(self, *, project_limit: int = 4, goal_limit: int = 8) -> list[dict]:
        with self.database.read() as session:
            projects = list(
                session.scalars(
                    select(Project)
                    .where(Project.status == "active")
                    .order_by(Project.updated_at.desc(), Project.id)
                    .limit(project_limit)
                )
            )
            snapshot: list[dict] = []
            remaining_goals = goal_limit
            for project in projects:
                goals: list[Goal] = []
                if remaining_goals > 0:
                    goals = list(
                        session.scalars(
                            select(Goal)
                            .where(
                                Goal.project_id == project.id,
                                Goal.status == "active",
                            )
                            .order_by(Goal.updated_at.desc(), Goal.id)
                            .limit(remaining_goals)
                        )
                    )
                    remaining_goals -= len(goals)
                snapshot.append(
                    {
                        "id": project.id,
                        "name": project.name,
                        "summary": project.summary,
                        "status": project.status,
                        "goals": [
                            {
                                "id": goal.id,
                                "title": goal.title,
                                "objective": goal.spec.get("objective"),
                                "status": goal.status,
                            }
                            for goal in goals
                        ],
                    }
                )
            return snapshot
