import { Link } from 'react-router-dom';

export function NotFound() {
  return (
    <main className="pair">
      <div className="card">
        <h2>Page not found</h2>
        <p className="hint">There is nothing at this address.</p>
        <Link className="link" to="/">
          Back to Deskhand
        </Link>
      </div>
    </main>
  );
}
