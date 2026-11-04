import { useId, useState, type InputHTMLAttributes, type ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  icon: IconName;
  hint?: string;
  extra?: ReactNode;
}

export function TextField({ label, icon, hint, extra, className, ...input }: FieldProps) {
  const id = useId();
  return (
    <div className="form-row">
      <label htmlFor={id}>{label}</label>
      <div className="field">
        <Icon name={icon} />
        <input id={id} className={className} {...input} />
        {extra}
      </div>
      {hint && <span className="hint">{hint}</span>}
    </div>
  );
}

export function PasswordField(props: Omit<FieldProps, "icon" | "type" | "extra">) {
  const [visible, setVisible] = useState(false);
  return (
    <TextField
      {...props}
      icon="lock"
      type={visible ? "text" : "password"}
      extra={
        <button
          type="button"
          className="eye"
          aria-label={visible ? "Masquer le mot de passe" : "Afficher le mot de passe"}
          onClick={() => setVisible((v) => !v)}
        >
          <Icon name={visible ? "eyeOff" : "eye"} />
        </button>
      }
    />
  );
}

export function ErrorBanner({ children }: { children: ReactNode }) {
  return (
    <div className="banner-error" role="alert">
      <Icon name="alert" />
      <span>{children}</span>
    </div>
  );
}
