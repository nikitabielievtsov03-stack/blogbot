import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center px-6 py-20 md:py-28">
      <div className="w-full max-w-3xl">
        <header className="mb-12 space-y-4">
          <h1 className="text-5xl md:text-6xl font-bold tracking-tight">
            B-Roll <span className="text-primary">Finder</span>
          </h1>
          <p className="text-lg text-muted-foreground max-w-xl leading-relaxed">
            Paste your video transcript below. We&apos;ll break it into scenes
            and surface matching photos and videos from Pexels and Pixabay —
            ready to use as b-roll.
          </p>
        </header>

        <form className="space-y-6">
          <div className="space-y-2">
            <label htmlFor="transcript" className="block text-sm font-medium">
              Transcript
            </label>
            <Textarea
              id="transcript"
              name="transcript"
              rows={12}
              placeholder="Paste your full video transcript here…"
              className="min-h-[280px] text-base leading-relaxed resize-y"
            />
          </div>

          <div className="space-y-2">
            <label htmlFor="genre" className="block text-sm font-medium">
              Video genre{" "}
              <span className="text-muted-foreground font-normal">
                (optional)
              </span>
            </label>
            <input
              id="genre"
              name="genre"
              type="text"
              placeholder="vlog, tutorial, documentary, podcast…"
              className="flex h-10 w-full rounded-lg border border-input bg-transparent px-3 py-2 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30"
            />
          </div>

          <div className="pt-2">
            <Button
              type="submit"
              size="lg"
              className="h-11 px-6 text-base font-semibold"
              disabled
            >
              Find b-roll
            </Button>
            <p className="mt-3 text-xs text-muted-foreground">
              Backend wiring comes next — the button is a placeholder for now.
            </p>
          </div>
        </form>
      </div>
    </main>
  );
}
