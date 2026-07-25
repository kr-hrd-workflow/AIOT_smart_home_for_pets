import Link from "next/link";
import { LANDING_CHAPTERS } from "./landing-copy";

export function LandingOverlay() {
  const progressStops = [
    ["hero", "처음"],
    ...LANDING_CHAPTERS.map((chapter) => [chapter.id, chapter.navLabel]),
    ["final", "시작"],
  ] as const;

  return (
    <div className="landing-overlay">
      <header className="landing-header">
        <Link className="landing-wordmark" href="/" aria-label="PetCare 홈">
          PetCare
        </Link>
        <nav aria-label="주요 메뉴">
          <Link href="/demo">먼저 둘러보기</Link>
          <Link className="landing-header-cta" href="/dashboard">로그인</Link>
        </nav>
      </header>

      <div className="landing-copy-track">
        <nav className="landing-progress" aria-label="PetCare 스토리 진행">
          {progressStops.map(([id, label]) => (
            <a
              data-landing-target={id}
              href={`#story-${id}`}
              key={id}
              aria-label={label}
            >
              <span>{label}</span>
            </a>
          ))}
        </nav>
        {progressStops.map(([id]) => (
          <span
            className={`landing-scroll-anchor landing-scroll-anchor-${id}`}
            id={`story-${id}`}
            key={id}
          />
        ))}
        <section className="landing-hero" aria-labelledby="landing-title">
          <div className="landing-hero-copy">
            <p className="landing-kicker">혼자 있는 시간도, 곁에 있는 것처럼</p>
            <h1 id="landing-title">평범한 하루는 그대로. 달라진 순간만 알려드려요.</h1>
            <p className="landing-lede">
              식사와 휴식의 변화를 센서와 카메라가 함께 확인하고,
              필요한 장면만 짧게 기록해요.
            </p>
            <div className="landing-actions">
              <Link className="landing-primary" href="/signup">PetCare 시작하기</Link>
              <Link className="landing-secondary" href="/demo">먼저 둘러보기</Link>
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
              <div className={`landing-chapter-layout landing-chapter-${chapter.id}`}>
                <div className="landing-chapter-copy">
                  <p className="landing-chapter-eyebrow">{chapter.eyebrow}</p>
                  <h2 id={`${chapter.id}-title`}>{chapter.title}</h2>
                  <p className="landing-chapter-body">{chapter.body}</p>
                  {chapter.id === "connect" && (
                    <>
                      <div className="landing-install-actions">
                        <Link className="landing-install-cta" href="/dashboard">
                          로그인하고 연결하기
                        </Link>
                        <Link
                          className="landing-install-download"
                          href="/dashboard"
                        >
                          Home Agent 설치하기
                        </Link>
                      </div>
                      <p className="landing-installer-note">
                        설치 파일은 아직 디지털 서명이 없어 Windows SmartScreen에
                        ‘알 수 없는 게시자’ 경고가 표시될 수 있습니다.
                      </p>
                    </>
                  )}
                </div>
                {chapter.id === "feeding" && (
                  <aside className="landing-sensor-signal" aria-label="식사 감지 예시 상태">
                    <p>오늘 08:12</p>
                    <strong>식사를 시작했어요</strong>
                    <span>그릇 센서 · 카메라 일치</span>
                  </aside>
                )}
                {chapter.id === "rest" && (
                  <aside className="landing-rest-readout" aria-label="휴식 감지 예시 상태">
                    <p>오늘의 휴식</p>
                    <strong>1시간 24분</strong>
                    <span>침대 센서 · 카메라 함께 확인</span>
                  </aside>
                )}
                {chapter.id === "events" && (
                  <ol className="landing-event-sequence" aria-label="이벤트 보관 흐름">
                    <li>변화 전</li>
                    <li>감지 순간</li>
                    <li>변화 후</li>
                    <li>7일 뒤 자동 삭제</li>
                  </ol>
                )}
                {chapter.id === "connect" && (
                  <ol className="landing-connection-flow" aria-label="기기 연결 흐름">
                    <li>
                      <strong>Home Agent</strong>
                      <span>계정과 집 연결</span>
                    </li>
                    <li>
                      <strong>Pico + Jetson</strong>
                      <span>센서와 카메라 등록</span>
                    </li>
                    <li>
                      <strong>PetCare Web</strong>
                      <span>상태와 이벤트 확인</span>
                    </li>
                  </ol>
                )}
              </div>
            </section>
          ))}
        </div>

        <section className="landing-final" aria-labelledby="landing-final-title">
          <div>
            <p>지금 시작해 보세요</p>
            <h2 id="landing-final-title">반려동물의 오늘을 지금 확인하세요</h2>
            <Link className="landing-primary" href="/signup">PetCare 시작하기</Link>
          </div>
        </section>
      </div>
    </div>
  );
}
