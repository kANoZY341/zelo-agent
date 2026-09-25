import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Zelo Workspace",
  description: "Talk to Zelo and observe authoritative Core work.",
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
