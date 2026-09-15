import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiFetch } from '../services/api';
import { useAuth } from '../context/AuthContext';

export default function AuthPage() {
  const [mode, setMode] = useState('login');
  const [form, setForm] = useState({
    name: '',
    email: '',
    phone: '',
    password: '',
    role: 'customer',
  });
  const [message, setMessage] = useState('');
  const navigate = useNavigate();
  const { setToken, setUser } = useAuth();

  async function handleSubmit(e) {
    e.preventDefault();
    setMessage('');

    try {
      const endpoint = mode === 'login' ? '/login' : '/register';
      const payload = mode === 'login'
        ? { email: form.email, password: form.password }
        : form;

      const response = await apiFetch(endpoint, {
        method: 'POST',
        body: JSON.stringify(payload),
      });

      setToken(response.token);
      setUser(response.user);
      setMessage(mode === 'login' ? 'Login successful.' : 'Registration successful.');
      navigate('/');
    } catch (error) {
      setMessage(error.message || 'Authentication failed.');
    }
  }

  function handleChange(e) {
    setForm((current) => ({ ...current, [e.target.name]: e.target.value }));
  }

  return (
    <section className="auth-shell">
      <div className="auth-card">
        <div className="auth-toggle">
          <button type="button" className={mode === 'login' ? 'active' : ''} onClick={() => setMode('login')}>
            Login
          </button>
          <button type="button" className={mode === 'register' ? 'active' : ''} onClick={() => setMode('register')}>
            Register
          </button>
        </div>

        <form onSubmit={handleSubmit} className="auth-form">
          {mode === 'register' && (
            <>
              <label>
                Full name
                <input name="name" value={form.name} onChange={handleChange} required />
              </label>
              <label>
                Phone number
                <input name="phone" value={form.phone} onChange={handleChange} required />
              </label>
              <label>
                Role
                <select name="role" value={form.role} onChange={handleChange}>
                  <option value="customer">Customer</option>
                  <option value="farmer">Farmer</option>
                  <option value="admin">Admin</option>
                </select>
              </label>
            </>
          )}

          <label>
            Email
            <input type="email" name="email" value={form.email} onChange={handleChange} required />
          </label>

          <label>
            Password
            <input type="password" name="password" value={form.password} onChange={handleChange} required />
          </label>

          {message && <p className="notice">{message}</p>}
          <button className="button primary full" type="submit">
            {mode === 'login' ? 'Login to AgriDirect' : 'Create account'}
          </button>
        </form>
      </div>
    </section>
  );
}
