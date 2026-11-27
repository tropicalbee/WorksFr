import { useState } from 'react'
import { Bookmark } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { createBookmarklet } from '@/lib/bookmarklet'

export function BookmarkletSetup({ showcase, origin }: { showcase: boolean; origin: string }) {
  const [status, setStatus] = useState('')
  const link = showcase ? '' : createBookmarklet(origin)

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(link)
      setStatus('Copied')
    } catch {
      setStatus('Could not copy the link. Try dragging it to your bookmarks bar.')
    }
  }

  return (
    <section id="bookmarklet" className="mt-8 rounded-lg border border-border bg-card p-5" aria-labelledby="bookmarklet-heading">
      <h2 id="bookmarklet-heading" className="!mt-0">Use a bookmarklet</h2>
      {showcase ? (
        <p>Use the bookmarklet with a local WorksFr installation. Open /try in your local copy to set it up.</p>
      ) : (
        <>
          <p>Drag this to your bookmarks bar, then click it on any tutorial page.</p>
          <ol className="mb-4 flex list-decimal flex-wrap gap-x-8 pl-5 text-sm">
            <li>Save Send to WorksFr</li>
            <li>Open your tutorial</li>
            <li>Click the bookmark</li>
          </ol>
          <div className="flex flex-wrap items-center gap-3">
            <a
              href="#bookmarklet"
              draggable
              className="inline-flex items-center gap-2 rounded-md border border-border px-3 py-1 text-sm"
              ref={anchor => {
                // React blocks javascript: href props. This locally generated URL is for dragging.
                anchor?.setAttribute('href', link)
              }}
              onClick={event => event.preventDefault()}
            >
              <Bookmark aria-hidden="true" />Send to WorksFr
            </a>
            <Button type="button" variant="outline" size="sm" onClick={() => void copyLink()}>Copy link</Button>
            <span className="metadata" aria-live="polite">{status}</span>
          </div>
          <details className="mt-4 text-sm">
            <summary className="cursor-pointer">Set it up without dragging</summary>
            <ol className="mt-2 list-decimal pl-5">
              <li>Copy the link.</li>
              <li>Create a bookmark named Send to WorksFr.</li>
              <li>Edit the bookmark and paste the link into its address field.</li>
            </ol>
          </details>
          <p className="metadata !mb-0 !mt-4">Code stays in your browser and your WorksFr tab until you click Check.</p>
        </>
      )}
    </section>
  )
}
