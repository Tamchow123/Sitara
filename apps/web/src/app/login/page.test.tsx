import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import LoginPage from "./page";

const push = vi.fn();
const replace = vi.fn();
let search = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace }),
  useSearchParams: () => search,
}));

const login = vi.fn();

vi.mock("@/lib/auth", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/auth")>();
  return {
    ...actual,
    useAuth: () => ({
      status: "anonymous",
      user: null,
      login,
      register: vi.fn(),
      logout: vi.fn(),
      refreshUser: vi.fn(),
    }),
  };
});

function submitCredentials() {
  fireEvent.change(screen.getByLabelText(/email address/i), {
    target: { value: "bride@example.com" },
  });
  fireEvent.change(screen.getByLabelText(/^password$/i), {
    target: { value: "Correct-Horse-Battery-2026!" },
  });
  fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
}

beforeEach(() => {
  search = new URLSearchParams();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("login page", () => {
  it("renders a generic accessible message for invalid credentials", async () => {
    login.mockResolvedValue({
      ok: false,
      code: "invalid_credentials",
      message: "Unable to sign in with those credentials.",
    });
    render(<LoginPage />);
    submitCredentials();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Unable to sign in with those credentials.");
    // The message must not reveal whether the account exists.
    expect(alert.textContent).not.toMatch(/exist|not found|unknown|password is wrong/i);
    expect(push).not.toHaveBeenCalled();
  });

  it("redirects to a validated internal next path after success", async () => {
    search = new URLSearchParams("next=/account");
    login.mockResolvedValue({
      ok: true,
      user: { id: "u1", email: "bride@example.com" },
    });
    render(<LoginPage />);
    submitCredentials();
    await waitFor(() => expect(push).toHaveBeenCalledWith("/account"));
  });

  it("rejects external and protocol-relative next destinations", async () => {
    search = new URLSearchParams("next=https://evil.example/phish");
    login.mockResolvedValue({
      ok: true,
      user: { id: "u1", email: "bride@example.com" },
    });
    render(<LoginPage />);
    submitCredentials();
    await waitFor(() => expect(push).toHaveBeenCalledWith("/account"));
    expect(push).not.toHaveBeenCalledWith(expect.stringContaining("evil.example"));
  });

  it("shows a rate-limit specific message without technical detail", async () => {
    login.mockResolvedValue({
      ok: false,
      code: "auth_rate_limited",
      message: "Too many attempts. Try again later.",
    });
    render(<LoginPage />);
    submitCredentials();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/too many attempts/i);
  });
});
