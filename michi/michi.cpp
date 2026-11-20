#include <iostream>
using namespace std;

bool incrementar_en_mapeo(int mapeo[], int x, int y){
    mapeo[x]++;
    mapeo[y+3]++;
    if (x == y) mapeo[6]++; //diagonal principal
    if (x + y == 2) mapeo[7]++; //diagonal secundaria
    if (mapeo[x]==3 or mapeo[y+3]==3 or mapeo[6]==3 or mapeo[7]==3)
        return true;
    else return false;
}

void reiniciar_michi(char **michi, int mapeo_O[], int mapeo_X[]){
    for (int i = 0; i < 3; i++){
        michi[i][0] = '.';
        michi[i][1] = '.';
        michi[i][2] = '.';
    }
    for (int i = 0; i < 8; i++){
        mapeo_O[i] = 0;
        mapeo_X[i] = 0;
    }
}

bool peticion_de_reinicio(char **michi, int mapeo_O[], int mapeo_X[], int &contador){
    bool jugar_dnv;
    cout <<"quiere volver a jugar?\n 1: Si\n 0: No\n";
    cin >> jugar_dnv;
    if (jugar_dnv){
        reiniciar_michi(michi, mapeo_O, mapeo_X);
        contador = 0;
        return true;
    }
    else {
        cout << "Hasta la proxima.";
        return false;
    }  

}

int main(){
    int contador = 0;
    char **michi = new char*[3];
    for (int i = 0; i < 3; i++){
        michi[i] = new char[3];
        michi[i][0] = '.';
        michi[i][1] = '.';
        michi[i][2] = '.';
    }
    for (int i = 0; i < 3; i++){
        cout << michi[i][0] << ' ';
        cout << michi[i][1] << ' '; 
        cout << michi[i][2] << ' ';
        cout << endl;
    }
    bool turno = true; // 0: X, 1: O
    int mapeo_X[9]={0};
    int mapeo_O[9]={0};
    while (1){

        if (contador == 9){
            cout << "El tablero esta lleno. Se reinicia el juego.\n";
            if (!peticion_de_reinicio(michi, mapeo_O, mapeo_X, contador)) return 0;
            reiniciar_michi(michi, mapeo_O, mapeo_X);
            contador = 0;
        }

        cout << "Turno de " << (turno ? "O" : "X") << ": ingrese las coordenadas de la casilla: ";
        cout << "[fila][columna]\n";
        int x, y; 
        cin >> x >> y;
        while (x < 1 or x > 3 or y < 1 or y > 3){
            cout << "FUERA DEL RANGO! ingrese de nuevo [fila][columna].\n";
            cin >> x >> y;
        }
        x--;y--; 
        while (michi[x][y] != '.'){
            cout << "ya hay algo ahi, ingrese de nuevo [fila][columna].\n";
            cin >> x >> y;
            while (x < 1 or x > 3 or y < 1 or y > 3){ // debbugeado
                cout << "FUERA DEL RANGO! ingrese de nuevo [fila][columna].\n";
                cin >> x >> y;
            }
            x--;y--; // debuggeado
        }
        michi[x][y] = turno ? 'O' : 'X';
        contador++;
        for (int i = 0; i < 3; i++){
                cout << michi[i][0] << ' ';
                cout << michi[i][1] << ' '; 
                cout << michi[i][2] << ' ';
                cout << endl;
        }
        
        if (incrementar_en_mapeo(turno ? mapeo_O : mapeo_X, x, y)){
            cout << "EL JUGADOR " << (turno ? "O" : "X") << " GANOOO! \n"; 
            if (!peticion_de_reinicio(michi, mapeo_O, mapeo_X, contador)) return 0;
        }
        turno = !turno;
    }
    return 0;
}