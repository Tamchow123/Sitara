import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/lib/auth";
import { _resetCsrfTokenForTests } from "@/lib/api";
import AccountPage from "./page";

const push = vi.fn();
const replace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace }),
}));

const ME_AUTHENTICATED = {
  authenticated: true,
  user: { id: "22222222-2222-4222-8222-222222222222", email: "bride@example.com" },
};
const ME_ANONYMOUS = { authenticated: false, user: null };
const LOGOUT_BODY = {
  authenticated: false,
  user: null,
  csrf_token: "anonymous-token",
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// An empty gallery is the default so the account page's own tests keep
// testing the account page. Without it the list request would 404 and the
// gallery's error alert would sit alongside the sign-out error these tests
// assert on, making `findByRole("alert")` ambiguous.
const EMPTY_GALLERY = { designs: [], total: 0, limit: 20, offset: 0 };

function galleryRow(overrides: Record<string, unknown> = {}) {
  return {
    id: "33333333-3333-4333-8333-333333333333",
    title: "",
    display_title: "Ivory lehenga",
    status: "generated",
    created_at: "2026-03-04T10:00:00Z",
    updated_at: "2026-03-04T10:05:00Z",
    is_demo: true,
    version_count: 1,
    versions: [
      {
        id: "44444444-4444-4444-8444-444444444444",
        version_number: 1,
        is_demo: true,
        has_image: true,
        job_status: "succeeded",
        created_at: "2026-03-04T10:05:00Z",
      },
    ],
    ...overrides,
  };
}

function mockBackend(me: unknown, gallery: unknown = EMPTY_GALLERY) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/auth/me/") return json(me);
      if (url === "/api/v1/auth/csrf/") return json({ csrf_token: "t" });
      if (url === "/api/v1/auth/logout/") return json(LOGOUT_BODY);
      if (url.startsWith("/api/v1/designs/") && url.includes("/images/")) {
        return json({
          images: {
            original: { url: "https://signed.example/o", download_url: "https://signed.example/d", width: 800, height: 1000 },
            thumbnail: { url: "https://signed.example/t", width: 320, height: 400 },
            expires_at: "2026-03-04T11:00:00Z",
          },
        });
      }
      if (url.startsWith("/api/v1/designs/")) return json(gallery);
      return json({}, 404);
    }),
  );
}

function renderPage() {
  return render(
    <AuthProvider>
      <AccountPage />
    </AuthProvider>,
  );
}

beforeEach(() => {
  _resetCsrfTokenForTests();
});

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("account page", () => {
  it("redirects anonymous users to the login page", async () => {
    mockBackend(ME_ANONYMOUS);
    renderPage();
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith("/login?next=%2Faccount"),
    );
  });

  it("handles a stale session cookie: /me/ says anonymous, page redirects", async () => {
    // Simulates a browser still holding a sitara_sessionid cookie that the
    // server has flushed — middleware lets it through, /me/ is authoritative.
    mockBackend(ME_ANONYMOUS);
    renderPage();
    expect(await screen.findByText(/redirecting to sign in/i)).toBeInTheDocument();
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith("/login?next=%2Faccount"),
    );
  });

  it("shows only the canonical email for an authenticated user", async () => {
    mockBackend(ME_AUTHENTICATED);
    renderPage();
    expect(await screen.findByText("bride@example.com")).toBeInTheDocument();
    // No UUID leaks into the page.
    expect(document.body.textContent).not.toContain("22222222-2222");
  });

  it("failed logout keeps the user authenticated, shows an error and does not redirect", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/auth/me/") return json(ME_AUTHENTICATED);
        if (url === "/api/v1/auth/csrf/") return json({ csrf_token: "t" });
        if (url === "/api/v1/auth/logout/")
          return json({ error: { code: "auth_unavailable", message: "down" } }, 503);
        if (url.startsWith("/api/v1/designs/")) return json(EMPTY_GALLERY);
        return json({}, 404);
      }),
    );
    renderPage();
    const button = await screen.findByRole("button", { name: /sign out/i });
    fireEvent.click(button);

    // Accessible failure message; session may still be active server-side.
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/sign-out could not be completed/i);
    expect(alert).toHaveTextContent(/session may still be active/i);

    // No redirect, account details still visible, state still authenticated.
    expect(push).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
    expect(screen.getByText("bride@example.com")).toBeInTheDocument();
    // The button is usable again for a retry.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /sign out/i })).toBeEnabled(),
    );
  });

  it("network failure during logout keeps authenticated state intact", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/auth/me/") return json(ME_AUTHENTICATED);
        if (url === "/api/v1/auth/csrf/") return json({ csrf_token: "t" });
        if (url === "/api/v1/auth/logout/") throw new TypeError("fetch failed");
        if (url.startsWith("/api/v1/designs/")) return json(EMPTY_GALLERY);
        return json({}, 404);
      }),
    );
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /sign out/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /sign-out could not be completed/i,
    );
    expect(push).not.toHaveBeenCalled();
    expect(screen.getByText("bride@example.com")).toBeInTheDocument();
  });

  it("shows the gallery once the session is confirmed", async () => {
    mockBackend(ME_AUTHENTICATED, { designs: [galleryRow()], total: 1, limit: 20, offset: 0 });
    renderPage();
    expect(
      await screen.findByRole("heading", { name: /your concepts/i }),
    ).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Ivory lehenga" })).toBeInTheDocument();
  });

  it("does not request the design list for an anonymous visitor", async () => {
    // The redirect is a navigation nicety; what matters here is that the page
    // never asks for designs on a session it has not confirmed. An expired
    // cookie would answer with an empty list, which reads as "you have made
    // nothing" — the most alarming possible lie to tell someone about their
    // own work.
    mockBackend(ME_ANONYMOUS);
    renderPage();
    await waitFor(() => expect(replace).toHaveBeenCalled());
    const urls = (fetch as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0]));
    expect(urls.some((url) => url.startsWith("/api/v1/designs/"))).toBe(false);
  });

  it("does not request the design list when the session check itself failed", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/auth/me/") throw new TypeError("fetch failed");
        if (url === "/api/v1/auth/csrf/") return json({ csrf_token: "t" });
        if (url.startsWith("/api/v1/designs/")) return json(EMPTY_GALLERY);
        return json({}, 404);
      }),
    );
    renderPage();
    // "unavailable" is NOT signed out — the page says it does not know, and
    // asks for nothing on a session it could not verify either way.
    expect(
      await screen.findByText(/account details cannot be loaded right now/i),
    ).toBeInTheDocument();
    const urls = (fetch as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0]));
    expect(urls.some((url) => url.startsWith("/api/v1/designs/"))).toBe(false);
    expect(replace).not.toHaveBeenCalled();
  });

  it("logout calls the API and moves auth state to anonymous", async () => {
    mockBackend(ME_AUTHENTICATED);
    renderPage();
    const button = await screen.findByRole("button", { name: /sign out/i });
    fireEvent.click(button);
    await waitFor(() => expect(push).toHaveBeenCalledWith("/"));
    const urls = (fetch as ReturnType<typeof vi.fn>).mock.calls.map((c) =>
      String(c[0]),
    );
    expect(urls).toContain("/api/v1/auth/logout/");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
});
