import { AuthCard } from "../../components/auth-card";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; reset?: string }>;
}) {
  const query = await searchParams;
  const unavailable = query.error === "unavailable";
  const emailNotConfirmed = query.error === "email_not_confirmed";
  return (
    <AuthCard title="로그인" description="PetCare 홈에 안전하게 연결합니다.">
      {query.reset === "1" && <p role="status">비밀번호가 변경되었습니다.</p>}
      {unavailable ? (
        <>
          <p role="status">
            실시간 연결 준비 중입니다. 현재 공개 데모를 확인해 주세요.
          </p>
          <p>
            <a href="/demo">데모 보기</a>
          </p>
        </>
      ) : (
        <>
          {query.error && (
            <p role="alert">
              {emailNotConfirmed
                ? "가입 확인 메일의 링크를 먼저 눌러 주세요."
                : "이메일 또는 기존 비밀번호를 확인하세요. 다시 가입해도 기존 비밀번호는 바뀌지 않습니다."}
            </p>
          )}
          <form className="auth-form" action="/auth/login" method="post">
            <label>
              이메일
              <input name="email" type="email" autoComplete="email" required />
            </label>
            <label>
              비밀번호
              <input
                name="password"
                type="password"
                autoComplete="current-password"
                required
              />
            </label>
            <button type="submit">로그인</button>
          </form>
          <p>
            <a href="/forgot-password">비밀번호 재설정</a>
          </p>
          <p>
            <a href="/signup">계정 만들기</a>
          </p>
        </>
      )}
    </AuthCard>
  );
}
