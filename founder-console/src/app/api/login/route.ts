import { NextRequest, NextResponse } from "next/server";

const SESSION_COOKIE = "founder_session";

export async function POST(request: NextRequest) {
  const { passcode } = await request.json();

  if (!process.env.FOUNDER_PASSCODE) {
    return NextResponse.json(
      { error: "FOUNDER_PASSCODE is not configured on the server" },
      { status: 500 }
    );
  }

  if (passcode !== process.env.FOUNDER_PASSCODE) {
    return NextResponse.json({ error: "Incorrect passcode" }, { status: 401 });
  }

  const res = NextResponse.json({ ok: true });
  res.cookies.set(SESSION_COOKIE, "ok", {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 24 * 7, // 7 days
  });
  return res;
}
