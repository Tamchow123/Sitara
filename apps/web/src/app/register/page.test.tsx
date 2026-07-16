import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import RegisterPage from "./page";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
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
});
