import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import RegisterPage from "./page";

const push = vi.fn();
let search = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
  useSearchParams: () => search,
}));

const register = vi.fn();

vi.mock("@/lib/auth", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/auth")>();
  return {
    ...actual,
    useAuth: () => ({
      status: "anonymous",
      user: null,
      login: vi.fn(),
      register,
      logout: vi.fn(),
      refreshUser: vi.fn(),
    }),
  };
});

function fillAndSubmit() {
  fireEvent.change(screen.getByLabelText(/email address/i), {
    target: { value: "bride@example.com" },
  });
  fireEvent.change(screen.getByLabelText(/^password \(/i), {
    target: { value: "short" },
  });
  fireEvent.change(screen.getByLabelText(/confirm password/i), {
    target: { value: "short" },
  });
  fireEvent.click(screen.getByRole("button", { name: /create account/i }));
}

beforeEach(() => {
  search = new URLSearchParams();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("registration page", () => {
  it("renders field-level password errors accessibly", async () => {
    register.mockResolvedValue({
      ok: false,
      code: "invalid_input",
      message: "Please correct the highlighted fields.",
      fields: {
        password: [
          "This password is too short. It must contain at least 12 characters.",
        ],
      },
    });
    render(<RegisterPage />);
    fillAndSubmit();

    const fieldAlert = await screen.findByText(/at least 12 characters\./i);
    expect(fieldAlert.closest('[role="alert"]')).not.toBeNull();

    // The password input is linked to its error list for screen readers.
    const passwordInput = screen.getByLabelText(/^password \(/i);
    expect(passwordInput).toHaveAttribute("aria-describedby", "password-errors");
    expect(document.getElementById("password-errors")).not.toBeNull();
  });

  it("redirects to the account page after successful registration", async () => {
    register.mockResolvedValue({
      ok: true,
      user: { id: "u1", email: "bride@example.com" },
    });
    render(<RegisterPage />);
    fillAndSubmit();
    await vi.waitFor(() => expect(push).toHaveBeenCalledWith("/account"));
  });

  // Phase 21: registration became a step inside the design journey, so it has to
  // return people to where they were sent from.
  it("returns to the review screen someone was sent here from", async () => {
    search = new URLSearchParams("next=/design/d1/review");
    register.mockResolvedValue({
      ok: true,
      user: { id: "u1", email: "bride@example.com" },
    });
    render(<RegisterPage />);
    fillAndSubmit();
    await vi.waitFor(() => expect(push).toHaveBeenCalledWith("/design/d1/review"));
  });

  it("refuses an off-site destination", async () => {
    search = new URLSearchParams("next=https://evil.example/phish");
    register.mockResolvedValue({
      ok: true,
      user: { id: "u1", email: "bride@example.com" },
    });
    render(<RegisterPage />);
    fillAndSubmit();
    await vi.waitFor(() => expect(push).toHaveBeenCalledWith("/account"));
  });

  it("carries the destination across to sign-in for someone who already has an account", async () => {
    search = new URLSearchParams("next=/design/d1/review");
    render(<RegisterPage />);
    // Scoped to the form's own panel: the app shell has its own Sign in link,
    // and that one is navigation rather than part of this journey.
    const panel = within(screen.getByRole("region", { name: /your details/i }));
    expect(panel.getByRole("link", { name: /sign in/i })).toHaveAttribute(
      "href",
      "/login?next=%2Fdesign%2Fd1%2Freview",
    );
  });
});
