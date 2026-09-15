export default function CategoryCard({ category }) {
  return (
    <div className="feature-box">
      <h3>{category.name}</h3>
      <p>{category.description || 'Fresh agricultural products from trusted farmers.'}</p>
    </div>
  );
}
