import { describe, expect, it } from "vitest";

import { scrubSentryEvent, type SentryEvent } from "./sentry-scrub";

describe("scrubSentryEvent", () => {
  it("removes request bodies, cookies, query strings and sensitive headers", () => {
    const event: SentryEvent = {
      request: {
        data: { note: "private refinement note" },
        cookies: "sitara_sessionid=secret",
        query_string: "X-Amz-Signature=abc123",
        headers: {
          Cookie: "sitara_sessionid=secret",
          Authorization: "Bearer token",
          "X-CSRFToken": "csrf",
          "User-Agent": "test",
        },
        url: "https://media.example.com/design/original.webp?X-Amz-Signature=abc123",
      },
    };
    const scrubbed = scrubSentryEvent(event);
    expect(scrubbed.request?.data).toBeUndefined();
    expect(scrubbed.request?.cookies).toBeUndefined();
    expect(scrubbed.request?.query_string).toBeUndefined();
    expect(scrubbed.request?.headers).toEqual({ "User-Agent": "test" });
    // The signed-URL query string is stripped from the URL.
    expect(scrubbed.request?.url).toBe("https://media.example.com/design/original.webp");
  });

  it("drops user identity", () => {
    const scrubbed = scrubSentryEvent({ user: { email: "a@b.test", id: "42" } });
    expect(scrubbed.user).toBeUndefined();
  });

  it("reduces exceptions to their type, dropping the message", () => {
    const scrubbed = scrubSentryEvent({
      exception: {
        values: [
          { type: "TypeError", value: "leaked user input in message" },
          { type: "Error", value: "another secret" },
        ],
      },
    });
    expect(scrubbed.exception?.values?.[0]).toEqual({ type: "TypeError", value: "" });
    expect(scrubbed.exception?.values?.[1]).toEqual({ type: "Error", value: "" });
    expect(JSON.stringify(scrubbed)).not.toContain("leaked user input");
  });

  it("is a no-op-safe on an event without a request", () => {
    expect(scrubSentryEvent({})).toEqual({});
  });
});

describe("scrubSentryEvent — the handoff fragment (Phase 22, ADR 0026)", () => {
  const TOKEN = "a-plaintext-handoff-secret-value";

  it("strips a fragment from a URL that has no query string at all", () => {
    // The regression that matters. `/r#<token>` is the customer's phone
    // holding a live bearer credential, and it has no `?` — so a scrub that
    // only fired on a query string sent the whole token to Sentry.
    const event = scrubSentryEvent({
      request: { url: `https://shop.example/r#${TOKEN}` },
    });

    expect(event.request?.url).toBe("https://shop.example/r");
    expect(JSON.stringify(event)).not.toContain(TOKEN);
  });

  it("strips both when a URL carries a query string and a fragment", () => {
    const event = scrubSentryEvent({
      request: { url: `https://shop.example/r?utm=x#${TOKEN}` },
    });

    expect(event.request?.url).toBe("https://shop.example/r");
  });

  it("leaves a plain URL alone", () => {
    const event = scrubSentryEvent({ request: { url: "https://shop.example/design/new" } });

    expect(event.request?.url).toBe("https://shop.example/design/new");
  });

  it("strips a fragment out of breadcrumb urls", () => {
    // A navigation or fetch breadcrumb carries a URL of its own, and Sentry
    // attaches breadcrumbs to the event alongside request.url.
    const event = scrubSentryEvent({
      breadcrumbs: [
        { category: "navigation", data: { from: "/design/new", to: `/r#${TOKEN}` } },
        { category: "fetch", data: { url: `https://shop.example/r#${TOKEN}` } },
      ],
    });

    expect(JSON.stringify(event)).not.toContain(TOKEN);
  });

  it("strips a fragment out of the enveloped breadcrumb shape too", () => {
    const event = scrubSentryEvent({
      breadcrumbs: { values: [{ message: `https://shop.example/r#${TOKEN}` }] },
    });

    expect(JSON.stringify(event)).not.toContain(TOKEN);
  });

  it("survives malformed breadcrumbs rather than throwing on the way to Sentry", () => {
    expect(() =>
      scrubSentryEvent({
        breadcrumbs: [null as never, { data: null as never }, { message: 7 as never }],
      }),
    ).not.toThrow();
  });
});
