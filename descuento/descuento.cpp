
#include <iostream>
using namespace std;
#include <vector>

struct Producto{
    string nombre;
    int price;
    int cantidad;
};

struct Respuesta{
    int Subtotal;
    float descuento;
    int costo_envio;
    int total;
};

Respuesta calcularTotal(vector<Producto> productos, int cupon){
    int subtotal = 0;
    Respuesta respuesta;
    for (const auto item : productos){
        subtotal+=item.price*item.cantidad;
    }
    respuesta.Subtotal = subtotal;
    respuesta.descuento = (cupon/100.0) * subtotal;
    respuesta.costo_envio = subtotal >= 100 ? 0 : 12;
    respuesta.total = subtotal - respuesta.descuento + respuesta.costo_envio;
    return respuesta;
}

void casosDePrueba() {
    int n, cupon;
    cout << "Cantidad de productos: ";
    if (!(cin >> n)) return;
    while (n < 0 or n > 15){
        cout << "Rangos no validos. Ingrese de nuevo. \n";
        cin >> n;
    }
    vector<Producto> productos(n);
    for (int i = 0; i < n; i++) {
        cout << "\nProducto " << i + 1 << endl;
        cout << "Nombre del producto " << ":";
        cin >> productos[i].nombre;
        cout << "Precio: ";
        cin >> productos[i].price;
        cout << "Cantidad: ";
        cin >> productos[i].cantidad;
    }

    cout << "\nCupon de descuento (va de 1 a 100): \n";
    cin >> cupon;
    while (cupon < 1 or cupon > 100){
    cout << "Rangos no validos. Ingrese de nuevo. \n";
    cin >> n;
    }


    Respuesta r = calcularTotal(productos, cupon);

    cout << "\nLos resultados de la prueba son: \n";
    cout << "Subtotal: " << r.Subtotal << endl;
    cout << "Descuento: " << r.descuento << endl;
    cout << "Costo envio: " << r.costo_envio << endl;
    cout << "Total: " << r.total << endl;
}

void casosDePrueba_Hardcodeados() {
    vector<Producto> productos1 = {
        {"Laptop", 150, 1},
        {"Mouse", 30, 2}
    };

    vector<Producto> productos2 = {
        {"Teclado", 40, 1},
        {"Cable", 10, 2}
    };
    
    vector<Producto> productos3 = {
        {"Monitor", 200, 1},
        {"Audifonos", 50, 2},
        {"USB", 20, 1}
    };

    vector<vector<Producto>> casos = {productos1, productos2, productos3};
    vector<int> cupones = {10, 25, 50};

    for (int i = 0; i < 3; i++) {
        Respuesta r = calcularTotal(casos[i], cupones[i]);
        cout << "\nCASO DE PRUEBA " << i + 1 << endl;
        cout << "Subtotal: " << r.Subtotal << endl;
        cout << "Descuento: " << r.descuento << endl;
        cout << "Costo envio: " << r.costo_envio << endl;
        cout << "Total: " << r.total << endl;
    }
}

int main() {
    // casosDePrueba();
    casosDePrueba_Hardcodeados();

    return 0;
}
