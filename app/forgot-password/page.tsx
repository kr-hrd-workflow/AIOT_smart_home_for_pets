import { AuthCard } from "../../components/auth-card";

export default async function ForgotPasswordPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; sent?: string }>;
}) {
  const query = await searchParams;
  return (
    <AuthCard
      title="비밀번호 재설정"
      description="등록된 이메일로 재설정 링크를 보냅니다."
    >
      {query.error && <p role="alert">재설정 이메일을 보낼 수 없습니다.</p>}
      {query.sent === "1" && (
        <p role="status">재설정 이메일을 보냈습니다.</p>
      )}
      <form className="auth-form" action="/auth/forgot-password" method="post">
        <label>
          이메일
          <input name="email" type="email" autoComplete="email" required />
        </label>
        <button type="submit">재설정 링크 보내기</button>
      </form>
      <p>
        <a href="/login">로그인으로 돌아가기</a>
      </p>
    </AuthCard>
  );
}
