import { useEffect } from 'react'
import { useLocation } from 'react-router'
import { SiteHeader } from '@/components/SiteHeader'
import { focusCheckInput } from '@/components/checkInput'
import { BentoCard } from '@/components/landing/BentoCard'
import { CheckForm } from '@/components/landing/CheckForm'
import { FixDiffVisual } from '@/components/landing/FixDiffVisual'
import { HeroHeadline } from '@/components/landing/HeroHeadline'
import { LandingFooter } from '@/components/landing/LandingFooter'
import { RealRunVisual } from '@/components/landing/RealRunVisual'
import { RecentChecks } from '@/components/landing/RecentChecks'
import { RunLogVisual } from '@/components/landing/RunLogVisual'
import { VersionDriftVisual } from '@/components/landing/VersionDriftVisual'
import { VideoFrameVisual } from '@/components/landing/VideoFrameVisual'
import { DotPattern } from '@/components/ui/dot-pattern'
import { GridPattern } from '@/components/ui/grid-pattern'
import { ShineBorder } from '@/components/ui/shine-border'
import '@/components/landing/landing.css'

const HEADLINE_LEAD = 'Check whether a Python tutorial'
const HEADLINE_TAILS = [
  "still runs with today's libraries",
  'in a Jupyter notebook still runs',
  'from a YouTube video still works',
  'on Stack Overflow still works',
]

export default function HomePage() {
  const { hash, state, key } = useLocation()

  useEffect(() => {
    if (hash) document.getElementById(hash.slice(1))?.scrollIntoView()
  }, [hash])

  useEffect(() => {
    if ((state as { focusCheck?: boolean } | null)?.focusCheck) focusCheckInput()
  }, [state, key])

  return (
    <>
      <SiteHeader />
      <div className="lp-page">
      <main>
        <div className="lp-hero">
          <p className="lp-eyebrow">Tutorial check</p>
          <HeroHeadline lead={HEADLINE_LEAD} tails={HEADLINE_TAILS} />
          <p className="lp-sub">
            Paste a link or upload a notebook. WorksFr runs each step in a sandbox with today's library versions and shows a verified fix for every line that broke.
          </p>
          <CheckForm />
        </div>
        <div className="lp-bento" id="how">
          <BentoCard
            className="lp-wide"
            title="A broken line gets a verified fix"
            text="The old call is struck out and the replacement sits under it, but only after it has run."
            border={<ShineBorder />}
          >
            <FixDiffVisual />
          </BentoCard>
          <BentoCard
            className="lp-small"
            title="Old pins, today's versions"
            text="The version the tutorial used next to the one it was checked with."
          >
            <VersionDriftVisual />
          </BentoCard>
          <BentoCard
            className="lp-small"
            title="Every step runs in a sandbox"
            text="Steps run in order and each one is labelled. Tutorial code never touches your machine."
            texture={<DotPattern />}
          >
            <RunLogVisual />
          </BentoCard>
          <BentoCard
            className="lp-small"
            title="Code read from videos"
            text="For YouTube links, the code on screen is read from the frame and timed."
            texture={<GridPattern />}
          >
            <VideoFrameVisual />
          </BentoCard>
          <BentoCard
            className="lp-small"
            title="Only a real run decides"
            text="A proposed patch is a fix only after the sandbox run passes."
            texture={<DotPattern />}
          >
            <RealRunVisual />
          </BentoCard>
        </div>
        <RecentChecks />
      </main>
      <LandingFooter />
      </div>
    </>
  )
}
