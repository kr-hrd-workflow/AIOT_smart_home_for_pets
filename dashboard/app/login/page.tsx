import { AuthCard } from "../../components/auth-card";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; reset?: string }>;
}) {
  const query = await searchParams;
  return (
    <AuthCard title="로그인" description="PetCare 홈에 안전하게 연결합니다.">
      {query.error && <p role="alert">이메일 또는 비밀번호를 확인하세요.</p>}
      {query.reset === "1" && <p role="status">비밀번호가 변경되었습니다.</p>}
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
        <a href="/forgot-password">비밀번호를 잊으셨나요?</a>
      </p>
      <p>
        <a href="/signup">계정 만들기</a>
      </p>
    </AuthCard>
  );
}
