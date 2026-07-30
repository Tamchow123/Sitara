import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AppShell } from "./AppShell";
import { axeViolations } from "@/test-utils/axe";

// The shell is the only thing on screen on every route, so its two promises are
// worth testing directly rather than through whichever page happens to render
// it: there is always a labelled way Home, and going Home is never the
// destructive action.

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ status: "anonymous", user: null }),
}));

describe("AppShell", () => {
  it("always offers a labelled Home link and a skip link to the main content", () => {
    render(
      <AppShell>
        <h1>A page</h1>
      </AppShell>,
    );
    const home = screen.getByRole("link", { name: "Sitara — Home" });
    expect(home).toHaveAttribute("href", "/");
    expect(within(home).getByText("Sitara")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /skip to main content/i })).toHaveAttribute(
      "href",
      "#main-content",
    );
    expect(document.querySelector("#main-content")).not.toBeNull();
  });

  describe("Home is not Start over", () => {
    // The whole point of the hint: on the questionnaire, generation and review
    // routes, leaving via Home must not read as throwing the work away. There
    // is deliberately NO "Start over" control in the shell — starting again
    // means visiting /design/new, so Home can never be the destructive action
    // by accident.
    it("shows the route's reassurance beside the brand when one is given", () => {
      render(
        <AppShell homeHint="Your answers are saved as you go.">
          <h1>Questionnaire</h1>
        </AppShell>,
      );
      expect(screen.getByText("Your answers are saved as you go.")).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Sitara — Home" })).toHaveAttribute("href", "/");
    });

    it("offers no destructive control of its own, and no confirm-free way to discard work", () => {
      render(
        <AppShell homeHint="Your answers are saved as you go.">
          <h1>Questionnaire</h1>
        </AppShell>,
      );
      expect(screen.queryByRole("button", { name: /start over|discard|delete|clear/i })).toBeNull();
      expect(screen.queryByRole("link", { name: /start over|discard|delete/i })).toBeNull();
      // Home points at the landing page, never at the new-design route, which
      // is the one that would abandon the current draft.
      for (const link of screen.getAllByRole("link")) {
        expect(link).not.toHaveAttribute("href", "/design/new");
      }
    });

    it("omits the hint entirely on routes that did not ask for one", () => {
      render(
        <AppShell>
          <h1>Privacy</h1>
        </AppShell>,
      );
      expect(screen.queryByText(/saved as you go/i)).toBeNull();
    });
  });

  it("links the two information pages from the footer, under a named nav", () => {
    render(
      <AppShell>
        <h1>A page</h1>
      </AppShell>,
    );
    const nav = screen.getByRole("navigation", { name: /about sitara/i });
    expect(within(nav).getByRole("link", { name: /privacy/i })).toHaveAttribute("href", "/privacy");
    expect(within(nav).getByRole("link", { name: /about concepts/i })).toHaveAttribute(
      "href",
      "/concepts",
    );
  });

  it("keeps no private identifier in any visible navigation label", () => {
    render(
      <AppShell homeHint="Your answers are saved as you go.">
        <h1>Questionnaire</h1>
      </AppShell>,
    );
    // A design or job UUID leaking into a header label would put a private id
    // in browser history and over a user's shoulder.
    const header = document.querySelector("header")!;
    expect(header.textContent).not.toMatch(
      /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i,
    );
  });

  it("has no axe violations", async () => {
    const { container } = render(
      <AppShell homeHint="Your answers are saved as you go.">
        <h1>A page</h1>
      </AppShell>,
    );
    expect(await axeViolations(container)).toHaveNoViolations();
  });
});
