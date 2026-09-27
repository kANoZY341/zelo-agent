export type AbortDeadline = {
  clear: () => void;
  didTimeout: () => boolean;
};

export function createAbortDeadline(
  controller: AbortController,
  timeoutMs: number,
): AbortDeadline {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    throw new Error("timeoutMs must be a positive finite number");
  }
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  return {
    clear: () => clearTimeout(timer),
    didTimeout: () => timedOut,
  };
}

export function pageBackfillOffset(
  itemCount: number,
  total: number,
  offset: number,
  pageSize: number,
): number | null {
  if (itemCount !== 0 || total <= 0 || offset <= 0) return null;
  return Math.max(0, offset - pageSize);
}

export function pendingAfterSettlement(current: number): number {
  return Math.max(0, current - 1);
}

export function shouldShowDetailLoading(
  campaignId: string | null,
  detailLoading: boolean,
  hasVisibleOverview: boolean,
  hasAcceptedCampaignOverview: boolean,
): boolean {
  return (
    campaignId !== null &&
    detailLoading &&
    !hasVisibleOverview &&
    !hasAcceptedCampaignOverview
  );
}
