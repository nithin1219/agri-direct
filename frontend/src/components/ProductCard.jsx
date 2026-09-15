export default function ProductCard({ product, onAddToCart }) {
  const rating = Number(product.rating || 0);

  return (
    <article className="product-card">
      <div className="product-image" aria-label={product.name} />
      <div className="product-content">
        <span className="product-tag">{product.category_name || 'Fresh produce'}</span>
        <h3>{product.name}</h3>
        <p className="meta-line">By {product.farmer_name || 'AgriDirect'}</p>
        <p className="meta-line">{product.quantity || 0} {product.unit || 'kg'} available</p>
        <div className="rating-row">
          <span>★ {Number.isFinite(rating) ? rating.toFixed(1) : '4.8'}</span>
          <span>{product.availability || 'available'}</span>
        </div>
        <div className="product-row">
          <strong>₹{Number(product.price || 0).toFixed(2)}</strong>
          <button type="button" className="button small primary" onClick={() => onAddToCart(product.id)}>
            Add to cart
          </button>
        </div>
      </div>
    </article>
  );
}
