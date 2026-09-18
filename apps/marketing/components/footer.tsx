import Image from "next/image";
import Link from "next/link";
import { DASHBOARD_URL, signupUrl } from "@/lib/api";

export function Footer() {
  return (
    <footer className="site-footer">
      <div className="wrap">
        <div className="cols">
          <div>
            <Image src="/brand/full-logo.jpg" alt="ParlioTec" width={150} height={88} style={{ borderRadius: 12 }} />
            <p className="brand-blurb">
              The UK-first AI phone assistant that answers every call, books real appointments across your team and keeps
              your customers&apos; data in the UK.
            </p>
          </div>
          <div>
            <h4>Product</h4>
            <Link href="/features/">Platform</Link>
            <Link href="/compare/">Why ParlioTec</Link>
            <Link href="/how-it-works/">How it works</Link>
            <Link href="/pricing/">Pricing</Link>
            <Link href="/demo/">Hear it live</Link>
          </div>
          <div>
            <h4>Solutions</h4>
            <Link href="/industries/#trades">Trades &amp; field service</Link>
            <Link href="/industries/#clinics">Clinics &amp; practices</Link>
            <Link href="/industries/#property">Property &amp; lettings</Link>
            <Link href="/industries/#professional">Professional services</Link>
            <Link href="/industries/#agencies">Agencies &amp; resellers</Link>
          </div>
          <div>
            <h4>Company</h4>
            <Link href="/security/">Trust &amp; security</Link>
            <Link href="/contact/">Contact sales</Link>
            <a href={signupUrl}>Start free trial</a>
            <a href={`${DASHBOARD_URL}/login`}>Customer sign in</a>
            <a href={`${DASHBOARD_URL}/status`}>Service status</a>
          </div>
          <div>
            <h4>Legal</h4>
            <Link href="/legal/terms/">Terms of service</Link>
            <Link href="/legal/privacy/">Privacy policy</Link>
            <Link href="/legal/cookies/">Cookie policy</Link>
            <Link href="/legal/acceptable-use/">Acceptable use</Link>
            <Link href="/legal/dpa/">Data processing agreement</Link>
            <Link href="/legal/call-recording/">Call recording notice</Link>
          </div>
        </div>
        <div className="legal-line">
          <span>© {new Date().getFullYear()} ParlioTec. All rights reserved.</span>
          <span>
            Made in the UK
            <Link href="/legal/privacy/">Privacy</Link>
            <Link href="/legal/terms/">Terms</Link>
            <Link href="/legal/cookies/">Cookies</Link>
          </span>
        </div>
      </div>
    </footer>
  );
}
