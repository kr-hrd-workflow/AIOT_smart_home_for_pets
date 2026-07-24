import Link from "next/link";
import { LANDING_CHAPTERS } from "./landing-copy";

export function LandingOverlay() {
  return (
    <div className="landing-overlay">
      <header className="landing-header">
        <Link className="landing-wordmark" href="/" aria-label="PetCare 홈">
          PetCare
        </Link>
        <nav aria-label="주요 메뉴">
          <Link href="/demo">데모 보기</Link>
          <Link className="landing-header-cta" href="/dashboard">로그인</Link>
        </nav>
      </header>

      <div className="landing-copy-track">
      <section className="landing-hero" aria-labelledby="landing-title">
        <div className="landing-hero-copy">
          <p className="landing-kicker">집에 없는 시간도 안심할 수 있게</p>
          <h1 id="landing-title">반려동물의 하루를 필요한 순간만 기록합니다</h1>
          <p className="landing-lede">
            웹캠과 Pico 2 W 센서가 식사와 휴식 변화를 함께 확인하고,
            이벤트가 생긴 순간만 짧게 보관합니다.
          </p>
          <div className="landing-actions">
            <Link className="landing-primary" href="/dashboard">로그인</Link>
            <Link className="landing-secondary" href="/demo">데모 보기</Link>
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
          <Link className="landing-primary" href="/dashboard">로그인</Link>
        </div>
      </section>
      </div>
    </div>
  );
}
