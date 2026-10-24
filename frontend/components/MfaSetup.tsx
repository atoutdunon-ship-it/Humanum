"use client";
/**
 * Enrôlement de la double authentification (commun aux organismes et à la super-administration).
 * Étapes : QR code + clé manuelle → code à 6 chiffres → codes de secours affichés une seule fois.
 */
import { useState, type FormEvent } from "react";

type Setup = { secret: string; otpauth_uri: string; qr_svg: string };
export type Activated = { recovery_codes: string[]; access_token?: string };
type Call = <T>(path: string, init?: RequestInit) => Promise<T>;

export function CodeField({ id, value, onChange, recovery = false }: { id: string; value: string; onChange: (v: string) => void; recovery?: boolean }) {
  return (
    <input id={id} className="code-input" required autoFocus value={value} onChange={e => onChange(e.target.value)}
      inputMode={recovery ? "text" : "numeric"} autoComplete="one-time-code" spellCheck={false}
      pattern={recovery ? "[A-Za-z0-9-]{10,11}" : "[0-9 ]{6,7}"} maxLength={recovery ? 11 : 7}
      placeholder={recovery ? "XXXXX-XXXXX" : "123 456"} />
  );
}

export function RecoveryCodes({ codes, onDone, label = "J'ai conservé mes codes, continuer" }: { codes: string[]; onDone: () => void; label?: string }) {
  const [saved, setSaved] = useState(false);
  const download = () => {
    const blob = new Blob([`HUMANUM CONSULTING — codes de secours\nChaque code ne sert qu'une fois.\n\n${codes.join("\n")}\n`], { type: "text/plain" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "humanum-codes-de-secours.txt"; a.click();
    URL.revokeObjectURL(a.href); setSaved(true);
  };
  return (
    <div className="mfa-codes">
      <p><strong>Codes de secours.</strong> Ils remplacent votre téléphone en cas de perte : chacun ne sert qu'une fois.
        Ils ne seront plus jamais affichés.</p>
      <ol className="codes">{codes.map(c => <li key={c}><code>{c}</code></li>)}</ol>
      <div className="mfa-actions">
        <button type="button" className="ghost" onClick={download}>Télécharger (.txt)</button>
        <button type="button" className="ghost" onClick={() => { navigator.clipboard?.writeText(codes.join("\n")); setSaved(true); }}>Copier</button>
        <button type="button" disabled={!saved} onClick={onDone}>{label}</button>
      </div>
      {!saved && <p className="muted">Téléchargez ou copiez vos codes pour continuer.</p>}
    </div>
  );
}

export default function MfaSetup({ call, base, onDone }: { call: Call; base: string; onDone: (a: Activated) => void }) {
  const [setup, setSetup] = useState<Setup | null>(null);
  const [code, setCode] = useState("");
  const [done, setDone] = useState<Activated | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const start = async () => {
    setError(null); setBusy(true);
    try { setSetup(await call<Setup>(`${base}/setup`, { method: "POST" })); } catch (e) { setError((e as Error).message); }
    setBusy(false);
  };
  const activate = async (e: FormEvent) => {
    e.preventDefault(); setError(null); setBusy(true);
    try { setDone(await call<Activated>(`${base}/activate`, { method: "POST", body: JSON.stringify({ code }) })); }
    catch (err) { setError((err as Error).message); setCode(""); }
    setBusy(false);
  };

  if (done) return <RecoveryCodes codes={done.recovery_codes} onDone={() => onDone(done)} />;
  if (!setup) return (
    <div className="mfa">
      <p>Protégez votre compte avec une application d'authentification (Google Authenticator, Microsoft Authenticator,
        1Password, Bitwarden…). À chaque connexion, un code à 6 chiffres vous sera demandé en plus du mot de passe.</p>
      <p><button type="button" onClick={start} disabled={busy}>Configurer la double authentification</button></p>
      {error && <p className="err" role="alert">{error}</p>}
    </div>
  );
  return (
    <form className="mfa" onSubmit={activate}>
      <ol className="mfa-steps">
        <li>Dans votre application d'authentification, ajoutez un compte en scannant ce QR code.</li>
      </ol>
      {/* SVG produit par le serveur (segno) à partir de l'URI otpauth : aucun contenu saisi par l'utilisateur. */}
      <div className="qr" role="img" aria-label="QR code de configuration" dangerouslySetInnerHTML={{ __html: setup.qr_svg }} />
      <p className="muted">Impossible de scanner ? Saisissez cette clé :</p>
      <p className="secret"><code>{setup.secret.match(/.{1,4}/g)?.join(" ")}</code></p>
      <ol className="mfa-steps" start={2}><li><label htmlFor="mfa-code">Saisissez le code à 6 chiffres affiché</label></li></ol>
      <CodeField id="mfa-code" value={code} onChange={setCode} />
      <p><button type="submit" disabled={busy}>Activer</button></p>
      {error && <p className="err" role="alert">{error}</p>}
    </form>
  );
}
