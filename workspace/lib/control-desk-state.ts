import type { CampaignOverview } from "./types";

export function recoverPreviousPageOffset(
  offset: number,
  total: number,
  itemCount: number,
  pageSize: number,
): number | null {
  if (itemCount === 0 && total > 0 && offset > 0) {
    return Math.max(0, offset - pageSize);
  }
  return null;
}

export function campaignOverviewForSelection(
  overview: CampaignOverview | null,
  campaignId: string | null,
): CampaignOverview | null {
  return overview?.campaign.id === campaignId ? overview : null;
}

export function visibleOverviewForOffsets(
  overview: CampaignOverview | null,
  budgetOffset: number,
  scheduleOffset: number,
): CampaignOverview | null {
  if (
    overview &&
    overview.budgets.offset === budgetOffset &&
    overview.schedules.offset === scheduleOffset
  ) {
    return overview;
  }
  return null;
}

export function shouldShowDetailLoading(
  campaignId: string | null,
  detailLoading: boolean,
  visibleOverview: CampaignOverview | null,
  campaignOverview: CampaignOverview | null,
): boolean {
  return Boolean(
    campaignId && detailLoading && !visibleOverview && !campaignOverview,
  );
}

export function canRefreshHierarchy(pendingCount: number): boolean {
  return pendingCount === 0;
}
