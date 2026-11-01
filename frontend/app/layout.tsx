import type { Metadata } from "next";
import { IBM_Plex_Sans } from "next/font/google";
import "./globals.css";

const sans = IBM_Plex_Sans({ variable: "--font-sans", subsets: ["latin"], weight: ["400", "500", "600"] });

export const metadata: Metadata = {
  title: "Counterfactual Accessibility Lab",
  description: "What-if questions about access to hospitals, clinics, pharmacies and schools in Algiers.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} antialiased`}>
      <body>{children}</body>
    </html>
  );
}
