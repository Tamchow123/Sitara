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

function mockBackend(me: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/auth/me/") return json(me);
      if (url === "/api/v1/auth/csrf/") return json({ csrf_token: "t" });
      if (url === "/api/v1/auth/logout/") return json(LOGOUT_BODY);
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
      expect(replace).toHaveBeenCalledWith("/login?next=/account"),
    );
  });

  it("handles a stale session cookie: /me/ says anonymous, page redirects", async () => {
    // Simulates a browser still holding a sitara_sessionid cookie that the
    // server has flushed — middleware lets it through, /me/ is authoritative.
    mockBackend(ME_ANONYMOUS);
    renderPage();
    expect(await screen.findByText(/redirecting to sign in/i)).toBeInTheDocument();
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith("/login?next=/account"),
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
