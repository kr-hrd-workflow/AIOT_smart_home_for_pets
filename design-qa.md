**Comparison Target**

- Source visual truth path: `dashboard/public/landing-apartment-photoreal-v3.webp`
- Implementation screenshot path: `.runtime/animated-landing-desktop-film.png`
- Direct comparison input: `.runtime/animated-landing-comparison.png`
- Responsive evidence: `.runtime/animated-landing-mobile.png`
- Scroll-state evidence: `.runtime/animated-landing-chapter.png`
- Viewport: desktop `1440 x 900` CSS px; mobile `390 x 844` CSS px; device scale factor `1`
- Pixels and normalization: source `1672 x 941`; implementation capture `1425 x 891`; both normalized with cover-crop to `720 x 450` and placed side by side in the `1440 x 450` comparison image.
- State: anonymous public landing, desktop film playing after poster fade, mobile film playing, second story chapter scrolled into view.

**Findings**

- No actionable P0, P1, or P2 differences remain.
- Fonts and typography: Korean display copy preserves `keep-all`, has a clear display/body/control hierarchy, and stays legible over the cinematic plate at both tested breakpoints.
- Spacing and layout rhythm: the desktop composition keeps the navigation, hero copy, and CTAs inside safe margins; the mobile stack has no horizontal overflow and preserves usable touch targets.
- Colors and visual tokens: cyan actions and amber eyebrow text remain restrained against the warm apartment/blue-hour palette, with sufficient dark gradient support for contrast.
- Image quality and asset fidelity: the implementation uses the approved photoreal apartment directly, preserves the room geometry and pet setup, and crossfades into a deterministic Remotion loop without low-poly or code-drawn scene replacement.
- Copy and content: the PetCare value proposition, webcam/Pico 2 W explanation, login, and demo actions are present and coherent.

**Comparison History**

- [P2] The film stage initially did not receive a scroll transform. Fix: moved the DOM stage choreography to its own GSAP ScrollTrigger lifecycle in `PetHomeCanvas`. Post-fix evidence: at scroll `2300`, the stage computed transform was `matrix(1.0356, 0, 0, 1.0356, -3.25565, -1.35839)`.
- [P2] The cached mobile video could be playing while the poster stayed opaque. Fix: mark the film ready on `loadedData` and `playing` in addition to `canPlay`. Post-fix evidence: the mobile video reported `readyState 4`, advancing `currentTime`, `data-ready="true"`, and computed opacity `1`.

**Focused Region Comparison**

- Typography/CTA region was inspected in the desktop and mobile captures because text wrapping, contrast, and touch sizing are fidelity-critical.
- Pet/room region was inspected in the second-chapter capture; the right-aligned copy reveals the dog, bed, bowl, camera signal, and realistic room depth without duplicate artifacts.

**Primary Interactions Tested**

- Desktop and mobile video autoplay, muted playback, looping, source selection, poster-to-film fade, and advancing playback time.
- Desktop scroll-linked stage scale/translation and alternating chapter composition.
- Responsive 390 px layout and horizontal-overflow check.
- Browser console errors: none.

**Open Questions**

- None blocking. The dog is intentionally revealed more clearly during the alternating story chapters than under the first hero copy.

**Implementation Checklist**

- [x] Preserve the approved photoreal source composition.
- [x] Use deterministic desktop/mobile cinematic loops.
- [x] Keep DOM film and R3F signal layer aligned during scroll.
- [x] Verify desktop/mobile playback and responsive layout in a real browser.
- [x] Verify no browser console errors.

**Follow-up Polish**

- [P3] A future art-directed source plate could reserve a larger empty copy-safe zone while keeping the dog visible above the fold; the current alternating chapters already reveal the pet clearly.

final result: passed
