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

export class PendingRequestTracker {
  #count = 0;

  begin(): void {
    this.#count += 1;
  }

  settle(): void {
    if (this.#count === 0) {
      throw new Error("pending request accounting underflow");
    }
    this.#count -= 1;
  }

  isIdle(): boolean {
    return this.#count === 0;
  }

  count(): number {
    return this.#count;
  }
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
