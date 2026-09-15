import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiFetch } from '../services/api';
import { useAuth } from '../context/AuthContext';

export default function CartPage() {
  const { token } = useAuth();
  const navigate = useNavigate();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function loadCart() {
      if (!token) {
        navigate('/auth');
        return;
      }

      try {
        const response = await apiFetch('/cart', {}, token);
        setItems(response.cart || []);
      } catch (error) {
        console.error(error);
      } finally {
        setLoading(false);
      }
    }

    loadCart();
  }, [navigate, token]);

  const subtotal = items.reduce((sum, item) => sum + Number(item.price || 0) * Number(item.quantity || 0), 0);

  if (loading) return <p className="page-message">Loading cart...</p>;
  if (!items.length) return <p className="page-message">Your cart is empty.</p>;

  return (
    <section className="page-shell">
      <h2>Your Cart</h2>
      <div className="cart-wrap">
        <div className="cart-list">
          {items.map((item) => (
            <div key={item.id} className="cart-item">
              <div className="product-image mini" />
              <div className="cart-copy">
                <h3>{item.name}</h3>
                <p>Farmer: {item.farmer_name}</p>
                <p>₹{Number(item.price).toFixed(2)} / {item.unit}</p>
              </div>
              <div className="quantity-box">Qty: {item.quantity}</div>
              <strong>₹{(Number(item.price) * Number(item.quantity)).toFixed(2)}</strong>
            </div>
          ))}
        </div>

        <aside className="summary-box">
          <h3>Order Summary</h3>
          <div className="summary-row"><span>Subtotal</span><strong>₹{subtotal.toFixed(2)}</strong></div>
          <div className="summary-row"><span>Delivery fee</span><strong>₹40.00</strong></div>
          <div className="summary-row total"><span>Total</span><strong>₹{(subtotal + 40).toFixed(2)}</strong></div>
          <button type="button" className="button primary full" onClick={() => navigate('/checkout')}>Proceed to Checkout</button>
        </aside>
      </div>
    </section>
  );
}
