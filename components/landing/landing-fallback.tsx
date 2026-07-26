export function LandingFallback() {
  return (
    <div className="landing-fallback" data-testid="landing-fallback" aria-hidden="true">
      <picture>
        <source
          media="(max-width: 767px)"
          srcSet="/landing-apartment-photoreal-mobile-v2.webp"
        />
        <img
          src="/landing-apartment-photoreal-v3.webp"
          alt=""
          width="1679"
          height="945"
        />
      </picture>
    </div>
  );
}
