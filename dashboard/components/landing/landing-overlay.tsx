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
            <Link
              className="landing-secondary"
              href="/dashboard"
            >
              로그인 후 Home Agent 설치
            </Link>
            <Link className="landing-secondary" href="/demo">데모 보기</Link>
          </div>
          <p className="landing-installer-note">
            베타 설치 파일은 아직 디지털 서명이 없어 Windows SmartScreen에
            ‘알 수 없는 게시자’ 경고가 표시될 수 있습니다.
          </p>
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
              {chapter.id === "connect" && (
                <>
                  <div className="landing-install-actions">
                    <Link className="landing-install-cta" href="/dashboard">
                      로그인하고 연결
                    </Link>
                    <Link
                      className="landing-install-download"
                      href="/dashboard"
                    >
                      로그인 후 Windows 베타 설치
                    </Link>
                  </div>
                  <p className="landing-installer-note">
                    베타 설치 파일은 아직 디지털 서명이 없어 Windows SmartScreen에
                    ‘알 수 없는 게시자’ 경고가 표시될 수 있습니다.
                  </p>
                </>
              )}
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
