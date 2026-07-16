import { NextResponse, type NextRequest } from "next/server";

// OPTIMISTIC navigation aid only: a cookie-presence check that spares
// signed-out visitors a flash of the account page. It performs NO network
// or database work and provides NO security — a forged or stale cookie
// passes it, and that is fine, because the account page re-verifies via
// /api/v1/auth/me/ and Django endpoint permissions remain the actual
// authorization boundary for every API request. Future design APIs must
// always enforce ownership server-side.
//
// Only /account redirects here (to /login). /login never redirects to
// /account in middleware, so a stale session cookie cannot create a loop.

const SESSION_COOKIE_NAME = "sitara_sessionid";

export function middleware(request: NextRequest) {
  if (!request.cookies.has(SESSION_COOKIE_NAME)) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.search = "";
    url.searchParams.set("next", request.nextUrl.pathname);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/account"],
};
