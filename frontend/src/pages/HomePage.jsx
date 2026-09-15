import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import CategoryCard from '../components/CategoryCard';
import ProductCard from '../components/ProductCard';
import { apiFetch } from '../services/api';
import { useAuth } from '../context/AuthContext';

const stats = [
  { label: 'Farmers onboarded', value: '2,400+' },
  { label: 'Orders delivered', value: '18,500+' },
  { label: 'Customer satisfaction', value: '96%' },
  { label: 'Avg. savings', value: '22%' },
];

export default function HomePage() {
  const { token } = useAuth();
  const [products, setProducts] = useState([]);
  const [categories, setCategories] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    async function loadData() {
      try {
        const [productsResponse, categoriesResponse] = await Promise.all([
          apiFetch('/products'),
          apiFetch('/categories'),
        ]);
        setProducts(productsResponse.products || []);
        setCategories(categoriesResponse.categories || []);
      } catch (err) {
        setError(err.message || 'Unable to load AgriDirect data right now.');
      } finally {
        setLoading(false);
      }
    }

    loadData();
  }, []);

  async function handleAddToCart(productId) {
    if (!token) {
      window.location.href = '/auth';
      return;
    }

    try {
      await apiFetch('/cart', {
        method: 'POST',
        body: JSON.stringify({ product_id: productId, quantity: 1 }),
      }, token);
      alert('Item added to cart.');
    } catch (err) {
      alert(err.message || 'Unable to add item.');
    }
  }

  return (
    <main>
      <section className="hero">
        <div className="hero-copy">
          <span className="eyebrow">Direct from Indian farms</span>
          <h1>Fresh From Farm. Direct to You.</h1>
          <p>
            AgriDirect helps customers buy fresh produce directly from trusted farmers,
            while giving farmers better pricing and fair market access without middlemen.
          </p>
          <div className="hero-actions">
            <Link to="/products" className="button primary">Shop Products</Link>
            <Link to="/auth" className="button secondary">Join as Farmer</Link>
          </div>
          <div className="hero-stats">
            {stats.map((item) => (
              <div key={item.label} className="mini-stat">
                <strong>{item.value}</strong>
                <span>{item.label}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="hero-visual" aria-label="Marketplace preview">
          <div className="visual-card large">
            <div className="card-header">
              <span>Today's harvest</span>
              <strong>Hyderabad</strong>
            </div>
            <div className="product-badge">Organic vegetables</div>
            <div className="visual-list">
              <div>
                <span>Tomatoes</span>
                <strong>₹72/kg</strong>
              </div>
              <div>
                <span>Bananas</span>
                <strong>₹54/dozen</strong>
              </div>
              <div>
                <span>Rice</span>
                <strong>₹52/kg</strong>
              </div>
            </div>
          </div>
          <div className="floating-box">2,300+ happy farmers</div>
        </div>
      </section>

      <section className="section">
        <div className="section-heading">
          <span className="eyebrow">Featured products</span>
          <h2>Fresh picks from nearby farms</h2>
        </div>

        {loading ? (
          <p>Loading products...</p>
        ) : error ? (
          <p className="notice error">{error}</p>
        ) : (
          <div className="product-grid">
            {products.slice(0, 8).map((product) => (
              <ProductCard key={product.id} product={product} onAddToCart={handleAddToCart} />
            ))}
          </div>
        )}
      </section>

      <section className="section alt">
        <div className="section-heading">
          <span className="eyebrow">Categories</span>
          <h2>Explore agricultural categories</h2>
        </div>
        <div className="feature-grid">
          {categories.map((category) => (
            <CategoryCard key={category.id} category={category} />
          ))}
        </div>
      </section>
    </main>
  );
}
