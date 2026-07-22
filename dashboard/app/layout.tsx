import type { Metadata } from "next";
import { headers } from "next/headers";
import "./globals.css";

const title = "PetCare 운영 현황";
const description = "카메라와 센서가 함께 확인한 상태만 표시합니다.";

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host = requestHeaders.get("x-forwarded-host") ?? requestHeaders.get("host") ?? "localhost:3000";
  const forwardedProtocol = requestHeaders.get("x-forwarded-proto")?.split(",")[0];
  const protocol = forwardedProtocol ?? (/^(localhost|127\.0\.0\.1)(:|$)/.test(host) ? "http" : "https");
  const origin = `${protocol}://${host}`;
  const imageUrl = new URL("/og.png", origin).toString();

  return {
    title,
    description,
    icons: {
      icon: "/favicon.svg",
      shortcut: "/favicon.svg",
    },
    openGraph: {
      title,
      description,
      url: new URL("/", origin).toString(),
      images: [{ url: imageUrl, width: 1200, height: 630, alt: title }],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: [imageUrl],
    },
  };
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
