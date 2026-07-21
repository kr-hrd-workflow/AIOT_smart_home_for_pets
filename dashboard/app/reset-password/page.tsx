import { AuthCard } from "../../components/auth-card";

export default async function ResetPasswordPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const query = await searchParams;
  return (
    <AuthCard title="새 비밀번호 설정" description="새 비밀번호를 입력하세요.">
      {query.error && <p role="alert">재설정 링크를 다시 요청하세요.</p>}
      <form className="auth-form" action="/auth/reset-password" method="post">
        <label>
          새 비밀번호
          <input
            name="password"
            type="password"
            autoComplete="new-password"
            required
          />
        </label>
        <button type="submit">비밀번호 변경</button>
      </form>
      <p>
        <a href="/login">로그인으로 돌아가기</a>
      </p>
    </AuthCard>
  );
}
