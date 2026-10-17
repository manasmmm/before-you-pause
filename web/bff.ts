import { NextRequest, NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { signServiceToken } from "@/lib/service-token";

export async function POST(req: NextRequest) {
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return NextResponse.json({ error: "unauthorised" }, { status: 401 });
  const body = (await req.json()) as { sip_id: string; action: "pause" | "reduce" | "stop" };
  try {
    const res = await fetch(`${process.env.ENGINE_URL}/v1/copilot/evaluate`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${signServiceToken(user.id)}` },
      body: JSON.stringify({ sip_id: body.sip_id, action: body.action }),
      signal: AbortSignal.timeout(800),
    });
    if (!res.ok) throw new Error(String(res.status));
    return NextResponse.json(await res.json());
  } catch {
    // Fail open: if the engine is slow or down, the existing SIP flow runs untouched.
    return NextResponse.json({ eligible: false, variant: "ineligible", skip_reasons: ["engine_unavailable"] });
  }
}
