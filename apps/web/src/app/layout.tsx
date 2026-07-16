import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Sitara",
  description:
    "AI-assisted South Asian bridalwear concept design — concept visualisation only.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
