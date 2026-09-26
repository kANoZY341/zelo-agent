"use client";

import { useEffect, useMemo, useState } from "react";
import type {
  Campaign,
  CampaignOverview,
  CampaignPage,
  Goal,
  GoalPage,
  Project,
  ProjectPage,
} from "@/lib/types";

const WORKSPACE_HEADERS = { "X-Zelo-Workspace": "1" };
const REFRESH_INTERVAL_MS = 5000;

async function parseJson<T>(response: Response): Promise<T> {
  const body = await response.json();
  if (!response.ok) {
    const detail =
      typeof body?.detail === "string" ? body.detail : `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return body as T;
}

function timestamp(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function eventDetail(details: Record<string, unknown>): string | null {
  const status = details.status;
  if (typeof status === "string") return status.replaceAll("_", " ");
  const reason = details.reason;
  if (typeof reason === "string" && reason) return reason.replaceAll("_", " ");
  return null;
}

function shortId(value: string | null): string {
  return value ? value.slice(0, 8) : "—";
}

export function ControlDesk({
  coreHealthy,
  onShowTalk,
}: {
  coreHealthy: boolean | null;
  onShowTalk: () => void;
}) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [goals, setGoals] = useState<Goal[]>([]);
  const [goalId, setGoalId] = useState<string | null>(null);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [campaignId, setCampaignId] = useState<string | null>(null);
  const [overview, setOverview] = useState<CampaignOverview | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedProject = useMemo(
    () => projects.find((project) => project.id === projectId) ?? null,
    [projects, projectId],
  );
  const selectedGoal = useMemo(
    () => goals.find((goal) => goal.id === goalId) ?? null,
    [goals, goalId],
  );
  const selectedCampaign = useMemo(
    () => campaigns.find((campaign) => campaign.id === campaignId) ?? null,
    [campaigns, campaignId],
  );

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    fetch("/api/projects?limit=100&offset=0", {
      headers: WORKSPACE_HEADERS,
      cache: "no-store",
    })
      .then((response) => parseJson<ProjectPage>(response))
      .then((page) => {
        if (cancelled) return;
        setProjects(page.items);
        setProjectId((current) =>
          current && page.items.some((item) => item.id === current)
            ? current
            : (page.items[0]?.id ?? null),
        );
      })
      .catch((loadError) => {
        if (!cancelled) {
          setError(
            loadError instanceof Error ? loadError.message : "project_load_failed",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  useEffect(() => {
    let cancelled = false;
    setGoals([]);
    setGoalId(null);
    setCampaigns([]);
    setCampaignId(null);
    setOverview(null);
    if (!projectId) return () => undefined;

    fetch(`/api/projects/${encodeURIComponent(projectId)}/goals?limit=100&offset=0`, {
      headers: WORKSPACE_HEADERS,
      cache: "no-store",
    })
      .then((response) => parseJson<GoalPage>(response))
      .then((page) => {
        if (cancelled) return;
        setGoals(page.items);
        setGoalId(page.items[0]?.id ?? null);
      })
      .catch((loadError) => {
        if (!cancelled) {
          setError(
            loadError instanceof Error ? loadError.message : "goal_load_failed",
          );
        }
      });

    return () => {
      cancelled = true;
    };
  }, [projectId, refreshKey]);

  useEffect(() => {
    let cancelled = false;
    setCampaigns([]);
    setCampaignId(null);
    setOverview(null);
    if (!goalId) return () => undefined;

    fetch(`/api/goals/${encodeURIComponent(goalId)}/campaigns?limit=100&offset=0`, {
      headers: WORKSPACE_HEADERS,
      cache: "no-store",
    })
      .then((response) => parseJson<CampaignPage>(response))
      .then((page) => {
        if (cancelled) return;
        setCampaigns(page.items);
        setCampaignId(page.items[0]?.id ?? null);
      })
      .catch((loadError) => {
        if (!cancelled) {
          setError(
            loadError instanceof Error
              ? loadError.message
              : "campaign_load_failed",
          );
        }
      });

    return () => {
      cancelled = true;
    };
  }, [goalId, refreshKey]);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const load = async () => {
      if (!campaignId) {
        setOverview(null);
        return;
      }
      setDetailLoading(true);
      try {
        const response = await fetch(
          `/api/campaigns/${encodeURIComponent(campaignId)}/overview`,
          {
            headers: WORKSPACE_HEADERS,
            cache: "no-store",
          },
        );
        const next = await parseJson<CampaignOverview>(response);
        if (!cancelled) {
          setOverview(next);
          setError(null);
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(
            loadError instanceof Error
              ? loadError.message
              : "campaign_overview_failed",
          );
        }
      } finally {
        if (!cancelled) setDetailLoading(false);
      }

      if (!cancelled) {
        timer = setTimeout(load, REFRESH_INTERVAL_MS);
      }
    };

    void load();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [campaignId, refreshKey]);

  const recentEvents = overview
    ? [...overview.events.items].sort((a, b) => b.sequence - a.sequence).slice(0, 8)
    : [];

  return (
    <>
      <aside className="desk-sidebar">
        <div className="desk-sidebar-head">
          <div>
            <p className="eyebrow">CONTROL DESK</p>
            <h2>Projects</h2>
          </div>
          <button
            className="desk-refresh"
            onClick={() => setRefreshKey((value) => value + 1)}
            disabled={loading}
            title="Refresh canonical Core state"
          >
            ↻
          </button>
        </div>

        <div className="desk-project-list">
          {loading ? (
            <div className="desk-empty">Loading projects…</div>
          ) : projects.length === 0 ? (
            <div className="desk-empty">
              No Core projects yet. Create one through Zelo/Core and it will appear here.
            </div>
          ) : (
            projects.map((project) => (
              <button
                key={project.id}
                className={`desk-project ${project.id === projectId ? "active" : ""}`}
                onClick={() => setProjectId(project.id)}
              >
                <span className="desk-project-name">{project.name}</span>
                <span className="desk-project-meta">
                  {project.status} · {timestamp(project.updated_at)}
                </span>
              </button>
            ))
          )}
        </div>

        <div className="desk-core-state">
          <span
            className={`status-dot ${coreHealthy === false ? "offline" : ""}`}
          />
          {coreHealthy === null
            ? "Checking Core"
            : coreHealthy
              ? "Canonical Core connected"
              : "Core unavailable"}
        </div>
      </aside>

      <section className="desk-main">
        <header className="desk-topbar">
          <div>
            <p className="eyebrow">PROJECT / PROGRESS</p>
            <h1>{selectedProject?.name ?? "Control Desk"}</h1>
          </div>
          <div className="desk-topbar-actions">
            <button className="mobile-view-button" onClick={onShowTalk}>
              Talk
            </button>
            {selectedProject ? (
              <span className="desk-status-pill">{selectedProject.status}</span>
            ) : null}
          </div>
        </header>

        <div className="desk-scroll">
          {error ? <div className="error-banner desk-error">{error}</div> : null}

          {!selectedProject ? (
            <div className="desk-hero">
              <p className="eyebrow">CORE-BACKED OPERATIONS</p>
              <h2>No project selected</h2>
              <p>
                This Desk is a live projection of Zelo Core. It does not create its
                own project, task, approval, budget or schedule state.
              </p>
            </div>
          ) : (
            <>
              <section className="desk-project-summary">
                <p>{selectedProject.summary || "No project summary yet."}</p>
                <span>Updated {timestamp(selectedProject.updated_at)}</span>
              </section>

              <section className="desk-section">
                <div className="desk-section-head">
                  <div>
                    <p className="eyebrow">GOALS</p>
                    <h2>What this project is trying to achieve</h2>
                  </div>
                  <span>{goals.length}</span>
                </div>
                {goals.length === 0 ? (
                  <div className="desk-empty-card">No goals attached to this project.</div>
                ) : (
                  <div className="goal-grid">
                    {goals.map((goal) => (
                      <button
                        key={goal.id}
                        className={`goal-card ${goal.id === goalId ? "active" : ""}`}
                        onClick={() => setGoalId(goal.id)}
                      >
                        <span className="goal-status">{goal.status}</span>
                        <strong>{goal.title}</strong>
                        <p>{goal.spec.objective}</p>
                      </button>
                    ))}
                  </div>
                )}
              </section>

              {selectedGoal ? (
                <section className="desk-section">
                  <div className="desk-section-head">
                    <div>
                      <p className="eyebrow">CAMPAIGNS</p>
                      <h2>{selectedGoal.title}</h2>
                    </div>
                    <span>{campaigns.length}</span>
                  </div>

                  <div className="goal-spec-strip">
                    <div>
                      <strong>Success criteria</strong>
                      <span>{selectedGoal.spec.success_criteria.length}</span>
                    </div>
                    <div>
                      <strong>Constraints</strong>
                      <span>{selectedGoal.spec.constraints.length}</span>
                    </div>
                    <div>
                      <strong>Updated</strong>
                      <span>{timestamp(selectedGoal.updated_at)}</span>
                    </div>
                  </div>

                  {campaigns.length === 0 ? (
                    <div className="desk-empty-card">
                      No campaigns attached to this goal.
                    </div>
                  ) : (
                    <div className="campaign-grid">
                      {campaigns.map((campaign) => (
                        <button
                          key={campaign.id}
                          className={`campaign-card ${campaign.id === campaignId ? "active" : ""}`}
                          onClick={() => setCampaignId(campaign.id)}
                        >
                          <div className="campaign-card-head">
                            <span className={`campaign-state ${campaign.status}`}>
                              {campaign.status}
                            </span>
                            <span>{campaign.task_count} task(s)</span>
                          </div>
                          <strong>{campaign.title}</strong>
                          <p>{campaign.summary || "No campaign summary."}</p>
                          <small>Updated {timestamp(campaign.updated_at)}</small>
                        </button>
                      ))}
                    </div>
                  )}
                </section>
              ) : null}
            </>
          )}

          <div className="desk-inline-inspector">
        <p className="eyebrow">CAMPAIGN / LIVE STATE</p>
        <h2>{overview?.campaign.title ?? selectedCampaign?.title ?? "Select a campaign"}</h2>

        {detailLoading && !overview ? (
          <div className="desk-empty-card">Loading campaign state…</div>
        ) : overview ? (
          <>
            <div className="campaign-overview-card">
              <div>
                <span>Status</span>
                <strong>{overview.campaign.status}</strong>
              </div>
              <div>
                <span>Tasks</span>
                <strong>{overview.campaign.task_count}</strong>
              </div>
              <div>
                <span>Events</span>
                <strong>{overview.events.total}</strong>
              </div>
              <div>
                <span>Schedules</span>
                <strong>{overview.schedules.total}</strong>
              </div>
            </div>

            <section className="desk-inspector-section">
              <div className="desk-inspector-title">
                <strong>Recent activity</strong>
                <span>polling Core</span>
              </div>
              {recentEvents.length === 0 ? (
                <div className="desk-empty-mini">No campaign events yet.</div>
              ) : (
                <div className="activity-list">
                  {recentEvents.map((event) => (
                    <div className="activity-item" key={event.id}>
                      <span className="activity-sequence">#{event.sequence}</span>
                      <div>
                        <strong>{event.event.replaceAll("_", " ")}</strong>
                        <span>
                          {event.task_id ? `task ${shortId(event.task_id)} · ` : ""}
                          {eventDetail(event.details) ?? timestamp(event.created_at)}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section className="desk-inspector-section">
              <div className="desk-inspector-title">
                <strong>Budgets</strong>
                <span>{overview.budgets.total}</span>
              </div>
              {overview.budgets.items.length === 0 ? (
                <div className="desk-empty-mini">No campaign budgets configured.</div>
              ) : (
                <div className="budget-list">
                  {overview.budgets.items.map((budget) => (
                    <div className="budget-row" key={budget.resource}>
                      <div>
                        <strong>{budget.resource.replaceAll("_", " ")}</strong>
                        <span>
                          {budget.spent_units} spent · {budget.reserved_units} reserved
                        </span>
                      </div>
                      <span>
                        {budget.available_units}/{budget.limit_units}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section className="desk-inspector-section">
              <div className="desk-inspector-title">
                <strong>Schedules</strong>
                <span>{overview.schedules.total}</span>
              </div>
              {overview.schedules.items.length === 0 ? (
                <div className="desk-empty-mini">No bounded Mind schedules.</div>
              ) : (
                <div className="schedule-list">
                  {overview.schedules.items.map((schedule) => (
                    <div className="schedule-row" key={schedule.id}>
                      <div>
                        <strong>{schedule.status}</strong>
                        <span>
                          {schedule.run_count}/{schedule.max_runs} runs
                        </span>
                      </div>
                      <span>{timestamp(schedule.next_run_at)}</span>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <div className="boundary-card">
              <strong>Projection only</strong>
              <p>
                Projects, goals, campaigns, events, budgets and schedules come from
                canonical Core APIs.
              </p>
              <p>The Desk does not own lifecycle or execution authority.</p>
            </div>
          </>
        ) : (
          <div className="desk-empty-card">
            Select a campaign to inspect its canonical activity and operating state.
          </div>
        )}

          </div>
        </div>
      </section>

      <aside className="desk-inspector">
        <p className="eyebrow">CAMPAIGN / LIVE STATE</p>
        <h2>{overview?.campaign.title ?? selectedCampaign?.title ?? "Select a campaign"}</h2>

        {detailLoading && !overview ? (
          <div className="desk-empty-card">Loading campaign state…</div>
        ) : overview ? (
          <>
            <div className="campaign-overview-card">
              <div>
                <span>Status</span>
                <strong>{overview.campaign.status}</strong>
              </div>
              <div>
                <span>Tasks</span>
                <strong>{overview.campaign.task_count}</strong>
              </div>
              <div>
                <span>Events</span>
                <strong>{overview.events.total}</strong>
              </div>
              <div>
                <span>Schedules</span>
                <strong>{overview.schedules.total}</strong>
              </div>
            </div>

            <section className="desk-inspector-section">
              <div className="desk-inspector-title">
                <strong>Recent activity</strong>
                <span>polling Core</span>
              </div>
              {recentEvents.length === 0 ? (
                <div className="desk-empty-mini">No campaign events yet.</div>
              ) : (
                <div className="activity-list">
                  {recentEvents.map((event) => (
                    <div className="activity-item" key={event.id}>
                      <span className="activity-sequence">#{event.sequence}</span>
                      <div>
                        <strong>{event.event.replaceAll("_", " ")}</strong>
                        <span>
                          {event.task_id ? `task ${shortId(event.task_id)} · ` : ""}
                          {eventDetail(event.details) ?? timestamp(event.created_at)}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section className="desk-inspector-section">
              <div className="desk-inspector-title">
                <strong>Budgets</strong>
                <span>{overview.budgets.total}</span>
              </div>
              {overview.budgets.items.length === 0 ? (
                <div className="desk-empty-mini">No campaign budgets configured.</div>
              ) : (
                <div className="budget-list">
                  {overview.budgets.items.map((budget) => (
                    <div className="budget-row" key={budget.resource}>
                      <div>
                        <strong>{budget.resource.replaceAll("_", " ")}</strong>
                        <span>
                          {budget.spent_units} spent · {budget.reserved_units} reserved
                        </span>
                      </div>
                      <span>
                        {budget.available_units}/{budget.limit_units}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section className="desk-inspector-section">
              <div className="desk-inspector-title">
                <strong>Schedules</strong>
                <span>{overview.schedules.total}</span>
              </div>
              {overview.schedules.items.length === 0 ? (
                <div className="desk-empty-mini">No bounded Mind schedules.</div>
              ) : (
                <div className="schedule-list">
                  {overview.schedules.items.map((schedule) => (
                    <div className="schedule-row" key={schedule.id}>
                      <div>
                        <strong>{schedule.status}</strong>
                        <span>
                          {schedule.run_count}/{schedule.max_runs} runs
                        </span>
                      </div>
                      <span>{timestamp(schedule.next_run_at)}</span>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <div className="boundary-card">
              <strong>Projection only</strong>
              <p>
                Projects, goals, campaigns, events, budgets and schedules come from
                canonical Core APIs.
              </p>
              <p>The Desk does not own lifecycle or execution authority.</p>
            </div>
          </>
        ) : (
          <div className="desk-empty-card">
            Select a campaign to inspect its canonical activity and operating state.
          </div>
        )}
      </aside>
    </>
  );
}
