import { LANDING_CHAPTERS } from "./landing-copy";

export function LandingOverlay() {
  return (
    <div className="landing-overlay">
      <header className="landing-header">
        <a className="landing-wordmark" href="/" aria-label="PetCare 홈">
          PetCare
        </a>
        <nav aria-label="주요 메뉴">
          <a href="/demo">데모 보기</a>
          <a className="landing-header-cta" href="/login">로그인</a>
        </nav>
      </header>

      <section className="landing-hero" aria-labelledby="landing-title">
        <div className="landing-hero-copy">
          <p className="landing-kicker">집에 없는 시간도 안심할 수 있게</p>
          <h1 id="landing-title">반려동물의 하루를 필요한 순간만 기록합니다</h1>
          <p className="landing-lede">
            웹캠과 Pico 2 W 센서가 식사와 휴식 변화를 함께 확인하고,
            이벤트가 생긴 순간만 짧게 보관합니다.
          </p>
          <div className="landing-actions">
            <a className="landing-primary" href="/login">로그인</a>
            <a className="landing-secondary" href="/demo">데모 보기</a>
          </div>
        </div>
      </section>

      <div className="landing-chapters">
        {LANDING_CHAPTERS.map((chapter) => (
          <section
            className="landing-chapter"
            id={chapter.id}
            key={chapter.id}
            aria-labelledby={`${chapter.id}-title`}
          >
            <div className="landing-chapter-copy">
              <h2 id={`${chapter.id}-title`}>{chapter.title}</h2>
              <p>{chapter.body}</p>
            </div>
          </section>
        ))}
      </div>

      <section className="landing-final" aria-labelledby="landing-final-title">
        <div>
          <p>나의 PetCare 홈</p>
          <h2 id="landing-final-title">필요한 순간을 바로 확인하세요</h2>
          <a className="landing-primary" href="/login">로그인</a>
        </div>
      </section>
    </div>
  );
}
