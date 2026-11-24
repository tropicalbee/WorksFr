import type { ReactNode } from 'react'
import { SiteHeader } from '@/components/SiteHeader'
import { LandingFooter } from '@/components/landing/LandingFooter'
import '@/components/landing/landing.css'
import './legal.css'

export function LegalLayout({ title, children }: { title: string; children: ReactNode }) {
  return (
    <>
      <SiteHeader />
      <div className="lp-page">
        <main className="legal">
          <h1>{title}</h1>
          <p className="legal-updated">Last updated 25 November 2026</p>
          {children}
        </main>
        <LandingFooter />
      </div>
    </>
  )
}
