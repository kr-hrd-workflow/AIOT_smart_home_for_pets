import { AuthCard } from "../../components/auth-card";

export default async function SignupPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; sent?: string }>;
}) {
  const query = await searchParams;
  return (
    <AuthCard
      title="계정 만들기"
      description="이메일 확인 후 하나의 PetCare 홈이 생성됩니다."
    >
      {query.error && <p role="alert">계정을 만들 수 없습니다.</p>}
      {query.sent === "1" && <p role="status">확인 이메일을 보냈습니다.</p>}
      <form className="auth-form" action="/auth/signup" method="post">
        <label>
          이메일
          <input name="email" type="email" autoComplete="email" required />
        </label>
        <label>
          비밀번호
          <input
            name="password"
            type="password"
            autoComplete="new-password"
            required
          />
        </label>
        <button type="submit">계정 만들기</button>
      </form>
      <p>
        <a href="/login">로그인으로 돌아가기</a>
      </p>
    </AuthCard>
  );
}
