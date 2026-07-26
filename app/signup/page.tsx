import { AuthCard } from "../../components/auth-card";

export default async function SignupPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; sent?: string }>;
}) {
  const query = await searchParams;
  const weakPassword = query.error === "weak_password";
  return (
    <AuthCard
      title="계정 만들기"
      description="이메일 확인을 마치면 PetCare를 시작할 수 있습니다."
    >
      {query.error && (
        <p role="alert">
          {weakPassword
            ? "비밀번호는 8자 이상으로 입력해 주세요."
            : "계정을 만들 수 없습니다. 입력 내용을 확인해 주세요."}
        </p>
      )}
      {query.sent === "1" && (
        <div className="auth-notice" role="status">
          <p>
            새 계정이라면 확인 메일이 도착합니다. 메일의 링크를 눌러야
            로그인할 수 있습니다.
          </p>
          <p>
            이미 가입한 이메일이라면 새 비밀번호로 바뀌지 않습니다.{" "}
            <a href="/forgot-password">비밀번호를 재설정해 주세요.</a>
          </p>
        </div>
      )}
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
            minLength={8}
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
