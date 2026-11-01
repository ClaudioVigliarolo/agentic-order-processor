import { useEffect, useMemo, useState } from 'react';

const API_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';

async function api(path, { token, ...options } = {}) {
  const headers = { 'Content-Type': 'application/json', ...options.headers };
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`${API_URL}${path}`, { ...options, headers });
  const data = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 && token) {
      const expired = new Error('Your session expired. Please sign in again.');
      expired.authExpired = true;
      throw expired;
    }
    const detail = data?.detail;
    throw new Error(typeof detail === 'string' ? detail : detail?.message || 'Request failed.');
  }
  return data;
}

function formatMoney(euros) {
  return new Intl.NumberFormat('en-IE', {
    style: 'currency',
    currency: 'EUR',
    maximumFractionDigits: 0,
  }).format(euros);
}

function Login({ onLogin }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const result = await api('/api/login', {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      });
      onLogin(result.access_token, email);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-shell">
      <div className="login-card">
        <div className="brand-mark">OP</div>
        <p className="eyebrow">OWNER WORKSPACE</p>
        <h1>Review orders<br />before they go out.</h1>
        <p className="muted">Sign in to inspect AI-prepared orders and customer replies.</p>
        <form className="login-form" onSubmit={submit}>
          <label>
            Owner email
            <input
              autoComplete="username"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </label>
          <label>
            Password
            <input
              autoComplete="current-password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </label>
          {error && <p className="alert error">{error}</p>}
          <button className="button primary full-width" disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
        <p className="login-footnote">Your access token stays in this browser tab.</p>
      </div>
      <aside className="login-aside">
        <div className="aside-orbit orbit-one" />
        <div className="aside-orbit orbit-two" />
        <p className="eyebrow">A HUMAN IN THE LOOP</p>
        <h2>Thoughtful automation.<br />Your final say.</h2>
        <p>Orders remain drafts until you approve them. AI-written replies are for review only and are never sent here.</p>
        <div className="aside-step"><span>01</span> Review product and delivery details</div>
        <div className="aside-step"><span>02</span> Read the suggested customer reply</div>
        <div className="aside-step"><span>03</span> Approve or reject the order</div>
      </aside>
    </main>
  );
}

function OrderCard({ order, onAction, pending }) {
  const total = useMemo(
    () => order.items.reduce((sum, item) => sum + item.quantity * item.unit_price_euros, 0),
    [order.items],
  );

  function confirmAction(action) {
    const prompt = action === 'approve'
      ? 'Approve this order? Stock will be checked again and deducted.'
      : 'Reject this draft order?';
    if (window.confirm(prompt)) onAction(order.id, action);
  }

  return (
    <article className="order-card">
      <header className="order-header">
        <div>
          <div className="order-id-row">
            <span className="order-number">Order #{order.id}</span>
            <span className={`status-pill ${order.status}`}>{order.status}</span>
          </div>
          <p className="customer-email">{order.customer_email}</p>
        </div>
        <div className="order-date">
          <span>Created</span>
          <time dateTime={order.created_at}>
            {new Date(order.created_at).toLocaleString()}
          </time>
        </div>
      </header>

      <section className="order-section">
        <div className="section-heading">
          <span className="section-index">01</span>
          <h3>Order details</h3>
        </div>
        <div className="items-table">
          {order.items.map((item, index) => (
            <div className="item-row" key={`${item.sku}-${index}`}>
              <div>
                <strong>{item.product_name}</strong>
                <span className="sku">{item.sku}</span>
              </div>
              <span className="quantity">× {item.quantity}</span>
              <span className="item-total">{formatMoney(item.quantity * item.unit_price_euros)}</span>
            </div>
          ))}
          <div className="total-row"><span>Order total</span><strong>{formatMoney(total)}</strong></div>
        </div>
        <div className="delivery-box">
          <span className="delivery-icon">↗</span>
          <div><span className="detail-label">DELIVER TO</span><p>{order.delivery_address}</p></div>
        </div>
      </section>

      <section className="order-section response-section">
        <div className="section-heading">
          <span className="section-index">02</span>
          <h3>Suggested customer reply</h3>
          <span className="ai-badge">AI DRAFT</span>
        </div>
        {order.response_body ? (
          <div className="reply-preview">
            <div className="reply-subject"><span>SUBJECT</span>{order.response_subject}</div>
            <p>{order.response_body}</p>
          </div>
        ) : (
          <div className="alert warning">No reply draft is saved for this order.</div>
        )}
        <p className="reply-disclaimer">This reply is a draft. Approving the order does not send it.</p>
      </section>

      {order.status === 'draft' && (
        <footer className="order-actions">
          <p>Approval rechecks stock and deducts it. Rejection closes this draft.</p>
          <div className="action-buttons">
            <button
              className="button quiet-danger"
              disabled={pending}
              onClick={() => confirmAction('reject')}
            >
              Reject
            </button>
            <button
              className="button primary"
              disabled={pending || !order.response_body}
              onClick={() => confirmAction('approve')}
            >
              {pending ? 'Working…' : !order.response_body ? 'Waiting for reply draft' : 'Approve order'} <span aria-hidden="true">→</span>
            </button>
          </div>
        </footer>
      )}
    </article>
  );
}

function Dashboard({ token, ownerEmail, onLogout }) {
  const [orders, setOrders] = useState([]);
  const [filter, setFilter] = useState('draft');
  const [loading, setLoading] = useState(true);
  const [pendingId, setPendingId] = useState(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  async function loadOrders() {
    setLoading(true);
    setError('');
    try {
      const query = filter === 'all' ? '' : `?status=${encodeURIComponent(filter)}`;
      setOrders(await api(`/api/orders${query}`, { token }));
    } catch (err) {
      if (err.authExpired) onLogout();
      else setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadOrders(); }, [filter, token]);

  async function takeAction(orderId, action) {
    setPendingId(orderId);
    setError('');
    setNotice('');
    try {
      await api(`/api/orders/${orderId}/${action}`, { method: 'POST', token });
      setNotice(action === 'approve'
        ? `Order #${orderId} approved. Stock was updated; no email was sent.`
        : `Order #${orderId} rejected.`);
      await loadOrders();
    } catch (err) {
      if (err.authExpired) onLogout();
      else setError(err.message);
    } finally {
      setPendingId(null);
    }
  }

  const drafts = orders.filter((order) => order.status === 'draft').length;

  return (
    <div className="dashboard-shell">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="Order review home"><span className="brand-mark small">OP</span><span>Order <b>review</b></span></a>
        <div className="topbar-right">
          <span className="owner-label"><span className="online-dot" />{ownerEmail}</span>
          <button className="button logout-button" onClick={onLogout}>Sign out</button>
        </div>
      </header>

      <main id="top" className="dashboard-main">
        <section className="page-intro">
          <div>
            <p className="eyebrow">ORDER MANAGEMENT</p>
            <h1>Good work starts<br />with a careful review.</h1>
            <p className="muted">Inspect each request and its suggested reply before you decide.</p>
          </div>
          <div className="summary-card"><span className="summary-icon">◎</span><div><strong>{drafts}</strong><span>drafts to review</span></div></div>
        </section>

        <div className="workflow-notice"><span>!</span><p><strong>Nothing is sent automatically.</strong> Approving confirms the order and updates stock. The suggested reply remains a draft.</p></div>

        <div className="list-toolbar">
          <div><h2>Orders</h2><span className="result-count">{orders.length} shown</span></div>
          <label className="filter-control">Show
            <select value={filter} onChange={(event) => setFilter(event.target.value)}>
              <option value="draft">Drafts</option>
              <option value="confirmed">Confirmed</option>
              <option value="cancelled">Rejected</option>
              <option value="all">All orders</option>
            </select>
          </label>
        </div>

        {notice && <p className="alert success">{notice}</p>}
        {error && <p className="alert error">{error}</p>}
        {loading ? (
          <div className="empty-state"><span className="spinner" />Loading orders…</div>
        ) : orders.length ? (
          <div className="orders-list">
            {orders.map((order) => (
              <OrderCard key={order.id} order={order} pending={pendingId === order.id} onAction={takeAction} />
            ))}
          </div>
        ) : (
          <div className="empty-state">
            <span className="empty-icon">✓</span>
            <h3>{filter === 'draft' ? 'You’re all caught up.' : 'No orders in this view.'}</h3>
            <p>New order drafts will appear here after the email workflow processes them.</p>
          </div>
        )}
      </main>
      <footer className="page-footer"><span>GMAIL ORDER PROCESSOR</span><span>Human approval required · No emails sent</span></footer>
    </div>
  );
}

export default function App() {
  const [session, setSession] = useState(null);

  if (!session) {
    return <Login onLogin={(token, email) => setSession({ token, email })} />;
  }
  return (
    <Dashboard
      token={session.token}
      ownerEmail={session.email}
      onLogout={() => setSession(null)}
    />
  );
}
