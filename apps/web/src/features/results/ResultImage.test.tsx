import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResultImage } from "./ResultImage";
import { DesignImageQueryError } from "./result-errors";
import type { DesignImages } from "@/lib/api";

function images(overrides: Partial<DesignImages> = {}): DesignImages {
  return {
    original: {
      url: "https://minio.local/signed-original",
      download_url: "https://minio.local/signed-original-download",
      width: 1536,
      height: 2048,
    },
    thumbnail: { url: "https://minio.local/signed-thumbnail", width: 384, height: 512 },
    expires_at: new Date(Date.now() + 5 * 60 * 1000).toISOString(),
    ...overrides,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ResultImage", () => {
  it("shows a loading state while pending", () => {
    render(
      <ResultImage
        images={undefined}
        isPending
        isFetching={false}
        error={null}
        altText="alt"
        onRetry={vi.fn()}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(/loading your image/i);
  });

  it("shows a controlled error state with a retry action when the fetch failed", () => {
    const onRetry = vi.fn();
    render(
      <ResultImage
        images={undefined}
        isPending={false}
        isFetching={false}
        error={new DesignImageQueryError(409, "design_image_not_ready", "not ready")}
        altText="alt"
        onRetry={onRetry}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/not ready/i);
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("shows a controlled 'not found' error state for a missing image", () => {
    render(
      <ResultImage
        images={undefined}
        isPending={false}
        isFetching={false}
        error={new DesignImageQueryError(404, "not_found", "not found")}
        altText="alt"
        onRetry={vi.fn()}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/not available/i);
  });

  it("shows a controlled 'unavailable' error state when the image service cannot be reached", () => {
    render(
      <ResultImage
        images={undefined}
        isPending={false}
        isFetching={false}
        error={new DesignImageQueryError(0, "unavailable", "The service could not be reached.")}
        altText="alt"
        onRetry={vi.fn()}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/could not be reached/i);
  });

  it("uses the original inline URL for the image and respects its dimensions", () => {
    render(
      <ResultImage
        images={images()}
        isPending={false}
        isFetching={false}
        error={null}
        altText="A model in a lehenga."
        onRetry={vi.fn()}
      />,
    );
    const img = screen.getByRole("img", { name: "A model in a lehenga." });
    expect(img).toHaveAttribute("src", "https://minio.local/signed-original");
    expect(img).toHaveAttribute("width", "1536");
    expect(img).toHaveAttribute("height", "2048");
    expect(img).toHaveAttribute("referrerpolicy", "no-referrer");
  });

  it("offers Annotate and Send to account in place of the download link", () => {
    // Phase 19 replaced Download entirely. Removing it is a UX decision, NOT a
    // privacy control — the signed URL is still a bearer URL and the browser can
    // still save a displayed image — so this asserts what the product now points
    // at, not that the image became unreachable.
    render(
      <ResultImage
        images={images()}
        isPending={false}
        isFetching={false}
        error={null}
        altText="alt"
        onRetry={vi.fn()}
        designId="design-1"
        versionId="version-1"
      />,
    );
    expect(screen.getByRole("link", { name: /annotate/i })).toHaveAttribute(
      "href",
      "/design/design-1/result/version-1/annotate",
    );
    expect(screen.getByRole("button", { name: /send to account/i })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /download image/i })).not.toBeInTheDocument();
  });

  it("discloses that sending takes the image out of Sitara, on this surface too", () => {
    // §8.5's disclosure has to be here and not only in the annotation workspace.
    // This button is one click from the concept screen and is the likelier first
    // send for someone who never opens Annotate, so a workspace-only sentence left
    // the more direct path undisclosed. It must also not claim the exposure was
    // removed — it is recorded as accepted.
    render(
      <ResultImage
        images={images()}
        isPending={false}
        isFetching={false}
        error={null}
        altText="alt"
        onRetry={vi.fn()}
        designId="design-1"
        versionId="version-1"
      />,
    );
    const note = screen.getByText(/may keep a copy outside sitara/i);
    expect(note).toBeInTheDocument();
    expect(note.textContent).toMatch(/your mail provider and your inbox/i);
  });

  it("omits both actions when it is not given a design and version", () => {
    // The comparison view renders two renders read-only; neither is the one the
    // page is "about", so neither gets an Annotate or Send action.
    render(
      <ResultImage
        images={images()}
        isPending={false}
        isFetching={false}
        error={null}
        altText="alt"
        onRetry={vi.fn()}
      />,
    );
    expect(screen.queryByRole("link", { name: /annotate/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /send to account/i })).not.toBeInTheDocument();
  });

  it("disables Send to account for an anonymous owner and says why", () => {
    // No account means no address the server could resolve, and the endpoint
    // deliberately has no field to supply one. The honest answer is to sign in.
    render(
      <ResultImage
        images={images()}
        isPending={false}
        isFetching={false}
        error={null}
        altText="alt"
        onRetry={vi.fn()}
        designId="design-1"
        versionId="version-1"
      />,
    );
    expect(screen.getByRole("button", { name: /send to account/i })).toBeDisabled();
    expect(screen.getByText(/sign in to send this to your email/i)).toBeInTheDocument();
  });

  it("opens the full-size image in a new tab with noreferrer noopener", () => {
    render(
      <ResultImage
        images={images()}
        isPending={false}
        isFetching={false}
        error={null}
        altText="alt"
        onRetry={vi.fn()}
      />,
    );
    const anchor = screen.getByRole("img", { name: "alt" }).closest("a");
    expect(anchor).toHaveAttribute("target", "_blank");
    expect(anchor).toHaveAttribute("rel", "noreferrer noopener");
  });

  it("does not render the image or its actions once past expiry", () => {
    render(
      <ResultImage
        images={images({ expires_at: new Date(Date.now() - 1000).toISOString() })}
        isPending={false}
        isFetching={false}
        error={null}
        altText="alt"
        onRetry={vi.fn()}
        designId="design-1"
        versionId="version-1"
      />,
    );
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /annotate/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /send to account/i })).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/expired/i);
  });

  it("shows a refreshing state (not an error) when past expiry but a refetch is in flight", () => {
    render(
      <ResultImage
        images={images({ expires_at: new Date(Date.now() - 1000).toISOString() })}
        isPending={false}
        isFetching
        error={null}
        altText="alt"
        onRetry={vi.fn()}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(/refreshing/i);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("triggers exactly one refresh on an image load failure", () => {
    const onRetry = vi.fn();
    render(
      <ResultImage
        images={images()}
        isPending={false}
        isFetching={false}
        error={null}
        altText="alt"
        onRetry={onRetry}
      />,
    );
    const img = screen.getByRole("img", { name: "alt" });
    fireEvent.error(img);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("does not loop: a second load failure for the SAME url does not retry again", () => {
    const onRetry = vi.fn();
    const { rerender } = render(
      <ResultImage
        images={images()}
        isPending={false}
        isFetching={false}
        error={null}
        altText="alt"
        onRetry={onRetry}
      />,
    );
    const img = screen.getByRole("img", { name: "alt" });
    fireEvent.error(img);
    fireEvent.error(img);
    // Simulate the parent re-rendering with the exact same (still-failing) URL.
    rerender(
      <ResultImage
        images={images()}
        isPending={false}
        isFetching={false}
        error={null}
        altText="alt"
        onRetry={onRetry}
      />,
    );
    fireEvent.error(screen.getByRole("img", { name: "alt" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("retries again after a genuine successful load, even for the same URL", () => {
    const onRetry = vi.fn();
    render(
      <ResultImage
        images={images()}
        isPending={false}
        isFetching={false}
        error={null}
        altText="alt"
        onRetry={onRetry}
      />,
    );
    const img = screen.getByRole("img", { name: "alt" });
    fireEvent.error(img);
    expect(onRetry).toHaveBeenCalledTimes(1);

    // A genuine successful load resets the once-per-episode retry guard.
    fireEvent.load(img);
    fireEvent.error(img);
    expect(onRetry).toHaveBeenCalledTimes(2);
  });

  it("does not keep auto-retrying across a sustained sequence of distinct failing URLs (caps at one retry per failure episode)", () => {
    // The backend mints a brand-new signed URL on every refresh, so a naive
    // URL-identity guard would never actually cap a sustained failure. This
    // proves the cap holds across several distinct URLs with no successful
    // load ever occurring in between.
    const onRetry = vi.fn();
    const { rerender } = render(
      <ResultImage
        images={images()}
        isPending={false}
        isFetching={false}
        error={null}
        altText="alt"
        onRetry={onRetry}
      />,
    );
    fireEvent.error(screen.getByRole("img", { name: "alt" }));
    expect(onRetry).toHaveBeenCalledTimes(1);

    for (let i = 0; i < 3; i += 1) {
      rerender(
        <ResultImage
          images={images({
            original: { ...images().original, url: `https://minio.local/fresh-${i}` },
          })}
          isPending={false}
          isFetching={false}
          error={null}
          altText="alt"
          onRetry={onRetry}
        />,
      );
      fireEvent.error(screen.getByRole("img", { name: "alt" }));
    }
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("never puts the signed URL into accessible text or error messages", () => {
    render(
      <ResultImage
        images={images()}
        isPending={false}
        isFetching={false}
        error={null}
        altText="alt"
        onRetry={vi.fn()}
      />,
    );
    expect(screen.queryByText(/minio\.local/i)).not.toBeInTheDocument();
  });
});
