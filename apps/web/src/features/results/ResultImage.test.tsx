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

  it("uses the download URL and a fixed filename for the download link", () => {
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
    const link = screen.getByRole("link", { name: /download image/i });
    expect(link).toHaveAttribute("href", "https://minio.local/signed-original-download");
    expect(link).toHaveAttribute("download", "sitara-concept.webp");
    expect(link).toHaveAttribute("referrerpolicy", "no-referrer");
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

  it("does not render the image or the download action once past expiry", () => {
    render(
      <ResultImage
        images={images({ expires_at: new Date(Date.now() - 1000).toISOString() })}
        isPending={false}
        isFetching={false}
        error={null}
        altText="alt"
        onRetry={vi.fn()}
      />,
    );
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /download image/i })).not.toBeInTheDocument();
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
