import posthog from "posthog-js";

export function initAnalytics(hashedId: string, analyticsConsent: boolean) {
  posthog.init(process.env.NEXT_PUBLIC_POSTHOG_KEY!, {
    api_host: process.env.NEXT_PUBLIC_POSTHOG_HOST,
    person_profiles: "identified_only",
    capture_pageview: false,
    autocapture: false,
    disable_session_recording: true,
    opt_out_capturing_by_default: true,
  });
  if (analyticsConsent) {
    posthog.opt_in_capturing();
    posthog.identify(hashedId);
  }
}

export const track = (event: string, props: Record<string, unknown> = {}) => posthog.capture(event, props);
