import { useEffect, useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { apiFetch } from '../services/api';
import { useAuth } from '../context/AuthContext';

const metricLabels = {
  customer: [
    { key: 'total_orders', label: 'Total orders' },
    { key: 'active_orders', label: 'Active orders' },
    { key: 'completed_orders', label: 'Completed orders' },
    { key: 'wishlist_items', label: 'Wishlist items' },
  ],
  farmer: [
    { key: 'total_products', label: 'Total products' },
    { key: 'active_products', label: 'Active products' },
    { key: 'orders_received', label: 'Orders received' },
    { key: 'completed_orders', label: 'Completed orders' },
    { key: 'total_sales', label: 'Total sales' },
    { key: 'total_earnings', label: 'Total earnings' },
  ],
  admin: [
    { key: 'total_users', label: 'Total users' },
    { key: 'total_farmers', label: 'Total farmers' },
    { key: 'total_customers', label: 'Total customers' },
    { key: 'total_products', label: 'Total products' },
    { key: 'total_orders', label: 'Total orders' },
    { key: 'total_revenue', label: 'Total revenue' },
  ],
};

export default function DashboardPage() {
  const { token, user } = useAuth();
  const navigate = useNavigate();
  const [data, setData] = useState({ stats: {}, recent_orders: [] });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function loadDashboard() {
      if (!token || !user) {
        navigate('/auth');
        return;
      }

      try {
        const response = await apiFetch('/dashboard', {}, token);
        setData(response);
      } catch (error) {
        console.error(error);
      } finally {
        setLoading(false);
      }
    }

    loadDashboard();
  }, [navigate, token, user]);

  if (!token) {
    return <Navigate to="/auth" replace />;
  }

  const labels = metricLabels[user?.role] || metricLabels.customer;

  return (
    <section className="page-shell">
      <div className="dashboard-header">
        <div>
          <span className="eyebrow">{user?.role || 'customer'} dashboard</span>
          <h2>Dashboard Overview</h2>
        </div>
      </div>

      {loading ? (
        <p className="page-message">Loading dashboard...</p>
      ) : (
        <>
          <div className="stats-grid">
            {labels.map((stat) => (
              <div key={stat.key} className="stat-card">
                <span>{stat.label}</span>
                <strong>{typeof data.stats[stat.key] === 'number' ? data.stats[stat.key] : '0'}</strong>
              </div>
            ))}
          </div>

          <div className="table-card">
            <h3>Recent orders</h3>
            {data.recent_orders.length ? (
              <table>
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Status</th>
                    <th>Total</th>
                    <th>City</th>
                  </tr>
                </thead>
                <tbody>
                  {data.recent_orders.map((order) => (
                    <tr key={order.id}>
                      <td>#{order.id}</td>
                      <td>{order.order_status || 'placed'}</td>
                      <td>₹{Number(order.total_amount || 0).toFixed(2)}</td>
                      <td>{order.city || 'Hyderabad'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="page-message">No recent activity yet.</p>
            )}
          </div>
        </>
      )}
    </section>
  );
}
