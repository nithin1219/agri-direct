import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiFetch } from '../services/api';
import { useAuth } from '../context/AuthContext';

export default function CheckoutPage() {
  const { token } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({
    delivery_address: '123 Gandhi Nagar',
    city: 'Hyderabad',
    state: 'Telangana',
    pincode: '500001',
    payment_method: 'cod',
  });
  const [message, setMessage] = useState('');

  function handleChange(e) {
    setForm((current) => ({ ...current, [e.target.name]: e.target.value }));
  }

  async function handleSubmit(e) {
    e.preventDefault();

    try {
      await apiFetch('/orders', {
        method: 'POST',
        body: JSON.stringify(form),
      }, token);
      setMessage('Order placed successfully.');
      setTimeout(() => navigate('/'), 1200);
    } catch (error) {
      setMessage(error.message || 'Unable to place order.');
    }
  }

  return (
    <section className="page-shell">
      <h2>Checkout</h2>
      <form className="checkout-form" onSubmit={handleSubmit}>
        <div className="checkout-grid">
          <div className="field-group">
            <label>
              Delivery Address
              <input name="delivery_address" value={form.delivery_address} onChange={handleChange} required />
            </label>
            <label>
              City
              <input name="city" value={form.city} onChange={handleChange} required />
            </label>
            <label>
              State
              <input name="state" value={form.state} onChange={handleChange} required />
            </label>
            <label>
              Pincode
              <input name="pincode" value={form.pincode} onChange={handleChange} required />
            </label>
          </div>

          <div className="field-group payment-box">
            <h3>Payment Method</h3>
            <label>
              <input type="radio" name="payment_method" value="cod" checked={form.payment_method === 'cod'} onChange={handleChange} />
              Cash on Delivery
            </label>
            <label>
              <input type="radio" name="payment_method" value="online" checked={form.payment_method === 'online'} onChange={handleChange} />
              Online Payment (future-ready)
            </label>
            <div className="summary-box compact">
              <div className="summary-row"><span>Delivery</span><strong>₹40.00</strong></div>
              <div className="summary-row total"><span>Total</span><strong>₹640.00</strong></div>
            </div>
          </div>
        </div>

        {message && <p className="notice">{message}</p>}
        <button className="button primary full" type="submit">Place Order</button>
      </form>
    </section>
  );
}
