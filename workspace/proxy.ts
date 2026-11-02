import { NextResponse, type NextRequest } from "next/server";
import { requireWorkspaceAccess } from "./lib/core";

// Authenticate the page before browser code can call the Core-authorized API.
// The browser's HTTP Basic credentials remain separate from Core's operator token.
export function proxy(request: NextRequest): NextResponse {
  const rejected = requireWorkspaceAccess(request);
  if (rejected) {
    return new NextResponse(rejected.body, {
      status: rejected.status,
      headers: rejected.headers,
    });
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/", "/api/:path*"],
};
